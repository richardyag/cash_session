from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CashWithdrawal(models.Model):
    """Movimiento de caja (ingreso o egreso) registrado durante una sesión abierta.

    Unifica todas las entradas/salidas de efectivo que NO son cobros de venta:
      EGRESOS  -> retiro de dueño (cuenta particular), pago a proveedor,
                  pago a empleado/jornalero, transferencia a otra caja, gasto.
      INGRESOS -> aporte de socio / ingreso vario, otro ingreso.

    Todo movimiento genera un account.payment (inbound/outbound) en el journal de
    efectivo de la caja → entra en el arqueo (cash.session.line cuenta inbound −
    outbound). La empresa es siempre COMERCIAL; no impacta la S.A.

    La transferencia entre cajas crea DOS pagos: egreso en la caja origen e ingreso
    en la caja destino (vía un partner puente "Transferencias entre cajas"), ambos
    marcados is_cash_transfer para quedar exentos del candado.
    """
    _name = 'cash.withdrawal'
    _description = 'Movimiento de caja'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    KIND_OUT = ('socio', 'proveedor', 'empleado', 'transfer', 'gasto')
    KIND_IN = ('aporte', 'ingreso')

    name = fields.Char(
        string='Referencia', default=lambda self: _('Nuevo'),
        readonly=True, copy=False,
    )
    kind = fields.Selection(
        [('socio', 'Retiro de dueño'),
         ('proveedor', 'Pago a proveedor'),
         ('empleado', 'Pago a empleado / jornalero'),
         ('transfer', 'Transferencia a otra caja'),
         ('gasto', 'Gasto / otro egreso'),
         ('aporte', 'Aporte / ingreso'),
         ('ingreso', 'Otro ingreso')],
        string='Concepto', required=True, default='socio', tracking=True,
    )
    direction = fields.Selection(
        [('out', 'Egreso (sale plata)'), ('in', 'Ingreso (entra plata)')],
        string='Tipo', compute='_compute_direction', store=True,
    )
    cash_register_id = fields.Many2one(
        'cash.register', string='Caja', required=True, tracking=True,
    )
    session_id = fields.Many2one(
        'cash.session', string='Sesión', tracking=True,
        domain="[('cash_register_id', '=', cash_register_id), ('state', '=', 'open')]",
        help='Sesión de caja abierta sobre la que se registra el movimiento.',
    )
    journal_id = fields.Many2one(
        'account.journal', string='Efectivo', required=True, tracking=True,
        domain="[('id', 'in', allowed_journal_ids)]",
    )
    allowed_journal_ids = fields.Many2many(
        'account.journal', compute='_compute_allowed_journals',
    )
    date = fields.Date(
        string='Fecha', default=fields.Date.context_today, required=True, tracking=True,
    )
    amount = fields.Monetary(
        string='Monto', required=True, currency_field='currency_id', tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Socio / beneficiario', tracking=True,
        help='Para retiro de dueño, pago a proveedor/empleado o aporte. El cargo '
             'va a la cuenta a pagar del contacto (cuenta particular para socios).',
    )
    dest_register_id = fields.Many2one(
        'cash.register', string='Caja destino', tracking=True,
        help='Para transferencia a otra caja: caja que recibe el efectivo.',
    )
    counterpart_account_id = fields.Many2one(
        'account.account', string='Cuenta (gasto / ingreso)',
        help='Para gasto u otro ingreso sin contacto: cuenta contable de contrapartida.',
    )
    memo = fields.Char(string='Motivo', tracking=True)
    state = fields.Selection(
        [('draft', 'Borrador'), ('posted', 'Registrado'), ('cancelled', 'Cancelado')],
        default='draft', tracking=True, string='Estado',
    )
    payment_id = fields.Many2one(
        'account.payment', string='Pago', readonly=True, copy=False,
    )
    payment_dest_id = fields.Many2one(
        'account.payment', string='Pago en caja destino', readonly=True, copy=False,
    )
    company_id = fields.Many2one(
        related='cash_register_id.company_id', store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True,
    )

    @api.depends('kind')
    def _compute_direction(self):
        for w in self:
            w.direction = 'in' if w.kind in self.KIND_IN else 'out'

    @api.depends('cash_register_id')
    def _compute_allowed_journals(self):
        for w in self:
            w.allowed_journal_ids = w.cash_register_id.journal_ids.filtered(
                lambda j: j.cash_session_kind == 'cash')

    @api.onchange('cash_register_id')
    def _onchange_cash_register(self):
        cash = self.cash_register_id.journal_ids.filtered(
            lambda j: j.cash_session_kind == 'cash')
        self.journal_id = cash[:1].id
        self.session_id = self.env['cash.session'].search([
            ('cash_register_id', '=', self.cash_register_id.id),
            ('state', '=', 'open'),
            ('responsible_id', '=', self.env.user.id)], limit=1).id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cash.withdrawal') or _('Nuevo')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def _check_common(self):
        if self.state != 'draft':
            raise UserError(_('Solo se registra un movimiento en borrador.'))
        if self.amount <= 0:
            raise UserError(_('El monto debe ser mayor a cero.'))
        if self.journal_id.cash_session_kind != 'cash' or self.journal_id not in self.cash_register_id.journal_ids:
            raise UserError(_('Elegí un journal de efectivo de la caja.'))
        if not self.session_id or self.session_id.state != 'open':
            raise UserError(_('Necesitás una sesión de caja ABIERTA para que el movimiento entre en el arqueo.'))

    def _ensure_transfer_partner(self):
        """Partner puente para transferencias entre cajas (cuenta de enlace)."""
        company = self.company_id
        Partner = self.env['res.partner']
        p = Partner.sudo().search([('ref', '=', 'TRANSF-CAJAS')], limit=1)
        if p:
            return p
        # cuenta de enlace (transitoria): usar la de transferencias de liquidez de la company
        acc = company.transfer_account_id
        if not acc:
            acc = self.env['account.account'].sudo().with_company(company).search([
                ('account_type', '=', 'asset_current'), ('reconcile', '=', True)], limit=1)
        p = Partner.sudo().create({'name': 'Transferencias entre cajas', 'ref': 'TRANSF-CAJAS'})
        if acc:
            p.with_company(company).write({
                'property_account_payable_id': acc.id,
                'property_account_receivable_id': acc.id})
        return p

    def _make_payment(self, ptype, partner, journal, amount, memo, transfer=False, dest_account=None):
        vals = {
            'payment_type': ptype,
            'partner_type': 'customer' if ptype == 'inbound' else 'supplier',
            'partner_id': partner.id if partner else False,
            'amount': amount,
            'date': self.date,
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'memo': memo,
            'is_cash_withdrawal': not transfer,
            'is_cash_transfer': transfer,
        }
        pay = self.env['account.payment'].sudo().with_company(self.company_id).create(vals)
        if dest_account:
            try:
                pay.write({'destination_account_id': dest_account.id})
            except Exception:
                pass
        pay.action_post()
        return pay

    def action_post(self):
        self.ensure_one()
        self._check_common()
        memo = self.memo or dict(self._fields['kind'].selection).get(self.kind)

        if self.kind == 'transfer':
            if not self.dest_register_id:
                raise UserError(_('Elegí la caja destino de la transferencia.'))
            dest_cash = self.dest_register_id.journal_ids.filtered(lambda j: j.cash_session_kind == 'cash')
            if not dest_cash:
                raise UserError(_('La caja destino no tiene journal de efectivo.'))
            bridge = self._ensure_transfer_partner()
            m = _('Transferencia %s → %s') % (self.cash_register_id.name, self.dest_register_id.name)
            self.payment_id = self._make_payment('outbound', bridge, self.journal_id, self.amount, m, transfer=True).id
            self.payment_dest_id = self._make_payment('inbound', bridge, dest_cash[:1], self.amount, m, transfer=True).id
            self.state = 'posted'
            return True

        # movimientos con partner (socio/proveedor/empleado/aporte)
        if self.kind in ('socio', 'proveedor', 'empleado', 'aporte'):
            if not self.partner_id:
                raise UserError(_('Elegí el contacto (socio / proveedor / empleado).'))
            partner_com = self.partner_id.with_company(self.company_id)
            if self.kind == 'socio' and not partner_com.property_account_payable_id:
                raise UserError(_('El socio "%s" no tiene cuenta particular (Cuenta a pagar) configurada.')
                                % self.partner_id.display_name)
            ptype = 'inbound' if self.direction == 'in' else 'outbound'
            self.payment_id = self._make_payment(ptype, self.partner_id, self.journal_id, self.amount, memo).id
            self.state = 'posted'
            return True

        # gasto / otro ingreso (sin contacto, con cuenta)
        if not self.counterpart_account_id:
            raise UserError(_('Elegí la cuenta de contrapartida para el %s.') % self.kind)
        ptype = 'inbound' if self.direction == 'in' else 'outbound'
        self.payment_id = self._make_payment(ptype, False, self.journal_id, self.amount, memo,
                                             dest_account=self.counterpart_account_id).id
        self.state = 'posted'
        return True

    def action_cancel(self):
        self.ensure_one()
        for pay in (self.payment_id | self.payment_dest_id):
            try:
                if pay.state not in ('draft', 'canceled'):
                    pay.action_draft()
                pay.action_cancel()
            except Exception:
                pass
        self.state = 'cancelled'

    def action_view_payment(self):
        self.ensure_one()
        if not self.payment_id:
            raise UserError(_('No hay pago generado.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'account.payment',
                'view_mode': 'form', 'res_id': self.payment_id.id}
