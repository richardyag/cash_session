from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.onchange('standard_price')
    def _onchange_standard_price_margen(self):
        for rec in self:
            if rec.x_margen:
                rec.list_price = round(rec.standard_price * rec.x_margen, 2)
