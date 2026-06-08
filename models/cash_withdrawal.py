from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CashWithdrawal(models.Model):
    """Extracción / retiro de caja.

    Registra dinero que SALE de una caja sin ser un pago a proveedor:
    principalmente retiros de los propietarios (socios), contra su cuenta
    particular. Operable solo por los usuarios del grupo "Extracciones de
    caja" (Marcos y Jorge).

    Mecánica contable:
    - Genera un account.payment OUTBOUND en el journal de efectivo de la caja,
      en la empresa COMERCIAL. Al ser un pago saliente, el arqueo lo descuenta
      del efectivo esperado (ver cash.session.line._compute_theoretical), así el
      cierre cuadra sin diferencia.
    - El partner (socio) debe tener configurada como cuenta a pagar su CUENTA
      PARTICULAR (una por socio) → el retiro carga esa cuenta.
    - El retiro SIEMPRE queda SOLO en COMERCIAL y NUNCA se replica a la S.A.
      (fiscal). Motivo: el efectivo del cajón es uno solo (blanco + negro
      mezclados, fungible), así que no se puede ni se debe etiquetar cada retiro
      como blanco o negro. La empresa comercial es la que administra la caja
      real; la S.A. solo refleja lo fiscal de las ventas, no los retiros.
    """
    _name = 'cash.withdrawal'
    _description = 'Extracción / retiro de caja'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Referencia', default=lambda self: _('Nueva'),
        readonly=True, copy=False,
    )
    cash_register_id = fields.Many2one(
        'cash.register', string='Caja', required=True, tracking=True,
    )
    session_id = fields.Many2one(
        'cash.session', string='Sesión', tracking=True,
        domain="[('cash_register_id', '=', cash_register_id), ('state', '=', 'open')]",
        help='Sesión de caja abierta sobre la que se registra el retiro. '
             'Debe estar abierta para que el retiro entre en el arqueo.',
    )
    journal_id = fields.Many2one(
        'account.journal', string='De (efectivo)', required=True, tracking=True,
        domain="[('id', 'in', allowed_journal_ids)]",
        help='Journal de efectivo del que se extrae la plata.',
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
        'res.partner', string='Socio / beneficiario', required=True, tracking=True,
        help='Propietario que retira. El cargo va a su cuenta particular '
             '(configurada como "Cuenta a pagar" del contacto).',
    )
    memo = fields.Char(string='Motivo', tracking=True)
    state = fields.Selection(
        [('draft', 'Borrador'), ('posted', 'Registrado'), ('cancelled', 'Cancelado')],
        default='draft', tracking=True, string='Estado',
    )
    payment_id = fields.Many2one(
        'account.payment', string='Pago (COMERCIAL)', readonly=True, copy=False,
    )
    company_id = fields.Many2one(
        related='cash_register_id.company_id', store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True,
    )

    @api.depends('cash_register_id')
    def _compute_allowed_journals(self):
        for w in self:
            w.allowed_journal_ids = w.cash_register_id.journal_ids.filtered(
                lambda j: j.cash_session_kind == 'cash'
            )

    @api.onchange('cash_register_id')
    def _onchange_cash_register(self):
        for w in self:
            cash_journals = w.cash_register_id.journal_ids.filtered(
                lambda j: j.cash_session_kind == 'cash'
            )
            # journal de efectivo por defecto (el primero)
            w.journal_id = cash_journals[:1].id
            # sesión abierta del usuario en esta caja
            sess = self.env['cash.session'].search([
                ('cash_register_id', '=', w.cash_register_id.id),
                ('state', '=', 'open'),
                ('responsible_id', '=', self.env.user.id),
            ], limit=1)
            w.session_id = sess.id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nueva')) == _('Nueva'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'cash.withdrawal') or _('Nueva')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_post(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_('Solo se puede registrar una extracción en borrador.'))
        if self.amount <= 0:
            raise UserError(_('El monto del retiro debe ser mayor a cero.'))
        if self.journal_id.cash_session_kind != 'cash':
            raise UserError(_('El retiro solo puede salir de un journal de efectivo.'))
        if self.journal_id not in self.cash_register_id.journal_ids:
            raise UserError(_('El journal no pertenece a la caja seleccionada.'))
        # Exigir sesión abierta a nombre del usuario (mismo criterio que el candado)
        if not self.session_id or self.session_id.state != 'open':
            raise UserError(_(
                'Necesitás una sesión de caja ABIERTA para registrar el retiro '
                '(para que quede reflejado en el arqueo).'))

        # Validar que el socio tenga cuenta particular (cuenta a pagar) en COMERCIAL
        partner_com = self.partner_id.with_company(self.company_id)
        if not partner_com.property_account_payable_id:
            raise UserError(_(
                'El socio "%(p)s" no tiene cuenta particular configurada en '
                '%(c)s (campo "Cuenta a pagar" del contacto). Pedile al contador '
                'que la configure antes de registrar el retiro.',
                p=self.partner_id.display_name, c=self.company_id.display_name))

        memo = self.memo or _('Retiro de socio %s') % self.partner_id.name
        # 1) Pago saliente en COMERCIAL (alimenta el arqueo)
        payment = self.env['account.payment'].with_company(self.company_id).create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner_id.id,
            'amount': self.amount,
            'date': self.date,
            'journal_id': self.journal_id.id,
            'company_id': self.company_id.id,
            'memo': memo,
            'is_cash_withdrawal': True,
        })
        payment.action_post()
        self.payment_id = payment.id
        self.state = 'posted'
        return True

    def action_cancel(self):
        self.ensure_one()
        for pay in self.payment_id:
            if pay.state == 'posted':
                try:
                    pay.action_draft()
                except Exception:
                    pass
            try:
                pay.action_cancel()
            except Exception:
                pass
        self.state = 'cancelled'

    def action_view_payment(self):
        self.ensure_one()
        if not self.payment_id:
            raise UserError(_('No hay pago generado.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'view_mode': 'form',
            'res_id': self.payment_id.id,
        }
