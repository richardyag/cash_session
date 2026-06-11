from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    cash_difference_account_id = fields.Many2one(
        'account.account',
        string='Diferencias de caja — Pérdida (faltante)',
        domain="[('company_ids', 'in', id)]",
        help='Cuenta de gasto donde se imputan los FALTANTES de caja '
             '(cuando el efectivo físico es menor al teórico).',
    )
    cash_difference_income_account_id = fields.Many2one(
        'account.account',
        string='Diferencias de caja — Ganancia (sobrante)',
        domain="[('company_ids', 'in', id)]",
        help='Cuenta de ingreso donde se imputan los SOBRANTES de caja '
             '(cuando el efectivo físico es mayor al teórico).',
    )
    cash_central_journal_id = fields.Many2one(
        'account.journal',
        string='Caja central (destino de transferencia al cierre)',
        domain="[('type', 'in', ['cash', 'bank']), ('company_id', '=', id)]",
        help='Journal al que se transfieren automáticamente al cierre de sesión '
             'el efectivo y los cheques de terceros recaudados. Las tarjetas NO '
             'se transfieren — quedan en su journal hasta acreditación bancaria.',
    )
