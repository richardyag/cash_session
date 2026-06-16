from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ProductPriceUpdate(models.TransientModel):
    _name = 'product.price.update'
    _description = 'Actualización de precios por familia'

    # Familia = campo Studio x_familia_id (Many2one a x_familia_producto) en
    # product.template. Para NO acoplar este módulo de código al modelo manual
    # de Studio en el setup del registry (puede romper el build), la familia se
    # ofrece como Selection dinámico leído en runtime, y guardamos el id como str.
    x_familia = fields.Selection(
        selection='_get_familia_options',
        string='Familia',
        required=True,
        help='Todos los productos de esta familia serán actualizados.',
    )
    pct_increase = fields.Float(
        string='Aumento %',
        required=True,
        default=0.06,
        help='Porcentaje de aumento. Ej: escribí 6 para +6%.',
    )
    product_count = fields.Integer(
        string='Productos a actualizar',
        compute='_compute_product_count',
    )

    @api.model
    def _get_familia_options(self):
        Familia = self.env.get('x_familia_producto')
        if Familia is None:
            return []
        return [(str(f.id), f.display_name) for f in Familia.search([], order='x_name')]

    def _familia_domain(self):
        """Domain de productos de la familia elegida."""
        return [
            ('x_familia_id', '=', int(self.x_familia)),
            ('active', '=', True),
        ]

    def _familia_label(self):
        Familia = self.env['x_familia_producto'].browse(int(self.x_familia))
        return Familia.display_name

    @api.depends('x_familia')
    def _compute_product_count(self):
        for rec in self:
            if rec.x_familia:
                rec.product_count = self.env['product.template'].search_count(
                    rec._familia_domain())
            else:
                rec.product_count = 0

    def action_apply(self):
        self.ensure_one()
        if not self.x_familia:
            raise UserError(_('Elegí una familia.'))
        if self.pct_increase == 0:
            raise UserError(_('El porcentaje de aumento no puede ser cero.'))

        # pct_increase viene del widget="percentage": el usuario escribe 6, se guarda 0.06
        factor = 1.0 + self.pct_increase
        products = self.env['product.template'].search(self._familia_domain())
        if not products:
            raise UserError(_('No se encontraron productos activos en la familia "%s".') % self._familia_label())

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
                ) % (count, self._familia_label(), self.pct_increase * 100),
                'type': 'success',
                'sticky': True,
            },
        }
