from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ProductPriceUpdate(models.TransientModel):
    _name = 'product.price.update'
    _description = 'Actualización de precios por categoría'

    categ_id = fields.Many2one(
        'product.category',
        string='Categoría de producto',
        required=True,
        help='Todos los productos de esta categoría (y subcategorías) serán actualizados.',
    )
    pct_increase = fields.Float(
        string='Aumento %',
        required=True,
        default=6.0,
        help='Porcentaje de aumento sobre el costo actual. Ej: 6 = +6%.',
    )
    product_count = fields.Integer(
        string='Productos a actualizar',
        compute='_compute_product_count',
    )

    @api.depends('categ_id')
    def _compute_product_count(self):
        for rec in self:
            if rec.categ_id:
                rec.product_count = self.env['product.template'].search_count([
                    ('categ_id', 'child_of', rec.categ_id.id),
                    ('active', '=', True),
                ])
            else:
                rec.product_count = 0

    def action_apply(self):
        self.ensure_one()
        if self.pct_increase == 0:
            raise UserError(_('El porcentaje de aumento no puede ser cero.'))

        factor = 1.0 + self.pct_increase / 100.0
        products = self.env['product.template'].search([
            ('categ_id', 'child_of', self.categ_id.id),
            ('active', '=', True),
        ])
        if not products:
            raise UserError(_('No se encontraron productos activos en la categoría "%s".') % self.categ_id.name)

        count = 0
        for p in products:
            new_cost = round(p.standard_price * factor, 2)
            vals = {'standard_price': new_cost}
            if p.x_margen:
                vals['list_price'] = round(new_cost * p.x_margen, 2)
            p.write(vals)
            count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Precios actualizados'),
                'message': _(
                    '%d productos de "%s" actualizados con +%.1f%%.'
                ) % (count, self.categ_id.name, self.pct_increase),
                'type': 'success',
                'sticky': True,
            },
        }
