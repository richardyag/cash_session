from odoo import fields, models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_open_payment_group(self):
        """Abre el formulario de Recibo (account.payment.group) pre-cargando
        las líneas pendientes de la factura. Permite registrar múltiples medios
        de pago en un solo recibo."""
        self.ensure_one()

        if self.payment_state == 'paid':
            raise UserError(_('Esta factura ya está pagada completamente.'))

        to_pay_lines = self.line_ids.filtered(
            lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable')
                      and not l.reconciled
        )
        if not to_pay_lines:
            raise UserError(_('No hay saldo pendiente en esta factura.'))

        is_inbound = self.move_type in ('out_invoice', 'out_receipt')
        pg = self.env['account.payment.group'].with_company(self.company_id).create({
            'company_id':    self.company_id.id,
            'partner_id':    self.partner_id.commercial_partner_id.id,
            'partner_type':  'customer' if is_inbound else 'supplier',
            'payment_type':  'inbound'  if is_inbound else 'outbound',
            'payment_date':  fields.Date.context_today(self),
            'to_pay_move_line_ids': [(4, l.id) for l in to_pay_lines],
        })

        return {
            'name':      _('Recibo'),
            'type':      'ir.actions.act_window',
            'res_model': 'account.payment.group',
            'view_mode': 'form',
            'res_id':    pg.id,
            'views':     [(False, 'form')],
            'target':    'current',
        }
