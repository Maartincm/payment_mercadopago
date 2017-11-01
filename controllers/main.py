# -*- coding: utf-8 -*-

try:
    import simplejson as json
except ImportError:
    import json
import logging
import pprint
import urllib2
import werkzeug
import time

from openerp import http, SUPERUSER_ID, _
from openerp.http import request
from openerp.http import Response

_logger = logging.getLogger(__name__)

from openerp.addons.payment_mercadopago.mercadopago import mercadopago

class MercadoPagoController(http.Controller):
    _notify_url = '/payment/mercadopago/ipn/'
    _return_url = '/payment/mercadopago/dpn/'
    _cancel_url = '/payment/mercadopago/cancel/'

    def _get_return_url(self, post):
        """ Extract the return URL from the data coming from MercadoPago. """

#        return_url = post.pop('return_url', '')
#        if not return_url:
#            custom = json.loads(post.pop('custom', False) or '{}')
#            return_url = custom.get('return_url', '/')
        return_url = ''
        return return_url

    def mercadopago_validate_data(self, post, status=False):
        """ MercadoPago IPN: three steps validation to ensure data correctness

         - step 1: return an empty HTTP 200 response -> will be done at the end
           by returning ''
         - step 2: POST the complete, unaltered message back to MercadoPago (preceded
           by cmd=_notify-validate), with same encoding
         - step 3: mercadopago send either VERIFIED or INVALID (single word)

        Once data is validated, process it. """
        res = False
        cr, uid, context = request.cr, request.uid, request.context
        topic = post.get('topic')
        type = post.get('type')
        reference = post.get('external_reference')
        op_id = post.get('data.id') or post.get('id', False)
        preference_id = post.get('preference_id')
        if not op_id and not preference_id:
            return res

        acquirer = request.env['payment.acquirer'].search([('name', '=', 'MercadoPago')])
        MPago = mercadopago.MP(acquirer.mercadopago_client_id, acquirer.mercadopago_secret_key)
        if op_id:
            payment_info = MPago.get_payment_info(op_id)

        if preference_id:
            preference = MPago.get_preference(preference_id)
            #TODO Check if we can get the amount paid from somewhere else more reliable
            amount_paid = preference['response']['items'][0]['unit_price']


        tx = None
        if reference:
            tx_ids = request.registry['payment.transaction'].search(cr, uid, [('reference', '=', reference)], context=context)
            if tx_ids:
                tx = request.registry['payment.transaction'].browse(cr, uid, tx_ids[0], context=context)
                _logger.info('mercadopago_validate_data() > payment.transaction: %s' % tx)


        _logger.info('MercadoPago: validating data')
        _logger.info('MercadoPago: %s' % post)
        if status == 'cancel':
            state = 'cancel'
        else:
            state = 'pending'

        status = post.get('collection_status')
        if status and status == 'approved' and tx and reference and amount_paid:
            state = 'done'
            #TODO See if this can be moved to somewhere else
            fee_line_model = request.env['student.fee.line']
            try:
                fee_line_id = int(reference.split('Cuota:')[1])
                fee_line = fee_line_model.browse(fee_line_id)
                wizard_register_payment = request.env['register.fee.payment']
                #TODO Use a proper Journal (Maybe in payment.configuration)
                #journal = request.env.ref("account.bank_journal")
                journal = request.env['account.journal'].sudo().search([])[0]
                #TODO Check all the sudos
                ctx = {'active_id': fee_line.id, 'active_ids': [fee_line.id]}
                payment_wiz = wizard_register_payment.\
                                with_context(ctx).sudo().create(dict(
                                date_paid=time.strftime("%Y-%m-%d"),
                                payment_method_id=journal.id,
                                amount_paid=amount_paid))

                payment_wiz.onchange_date_paid()
                payment_wiz.register_payment()

            except Exception as e:
                _logger.error(_("Error! Couldn't Register Payment for fee %s With Error: %s" % (reference, e)))

        # TODO Check get_payment_info with MPago Production

        if tx:
            transaction_vals = {'state': state,
                                }
            _logger.info('MercadoPago: ')
            tx.sudo().write(transaction_vals)

        return res

    @http.route('/payment/mercadopago/ipn/', type='json', auth='none', methods=['POST'])
    def mercadopago_ipn(self, **post):
        """ MercadoPago IPN. """
        # recibimo algo como http://www.yoursite.com/notifications?topic=payment&id=identificador-de-la-operación
        #segun el topic:
        # luego se consulta con el "id"
        post = request.httprequest.values.to_dict()
        # TODO: chequear las keys, sanitizar
        _logger.info('Beginning MercadoPago IPN form_feedback with post data %s', pprint.pformat(post))  # debug
        self.mercadopago_validate_data(post, 'ipn')
        Response.status = "200 OK"
        return {'status': "200 OK"}

    @http.route('/payment/mercadopago/dpn', type='http', auth="none")
    def mercadopago_dpn(self, **post):
        """ MercadoPago DPN """
        _logger.info('Beginning MercadoPago DPN form_feedback with post data %s', pprint.pformat(post))  # debug
        acquirer = request.env['payment.acquirer'].search([('name', 'ilike', 'mercadopago')])
        MPago = mercadopago.MP(acquirer.mercadopago_client_id, acquirer.mercadopago_secret_key)
        return_url = acquirer.mercadopago_base_url or self._get_return_url(post)
        self.mercadopago_validate_data(post, 'return')
        Response.status = "200 OK"
        _logger.info(_("Redirecting user to: %s" % return_url))
        return werkzeug.utils.redirect(return_url)

    @http.route('/payment/mercadopago/cancel', type='http', auth="none")
    def mercadopago_cancel(self, **post):
        """ When the user cancels its MercadoPago payment: GET on this route """
        cr, uid, context = request.cr, SUPERUSER_ID, request.context
        acquirer = request.env['payment.acquirer'].search([('name', 'ilike', 'mercadopago')])
        MPago = mercadopago.MP(acquirer.mercadopago_client_id, acquirer.mercadopago_secret_key)
        _logger.info('Beginning MercadoPago cancel with post data %s', pprint.pformat(post))  # debug
        return_url = acquirer.mercadopago_base_url or self._get_return_url(post)
        self.mercadopago_validate_data(post, 'cancel')
        Response.status = "200 OK"
        _logger.info(_("Redirecting user to: %s" % return_url))
        return werkzeug.utils.redirect(return_url)
