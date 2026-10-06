"""The texts are English in the source; the i18n files give Dutch, French and German (the language of the user decides)."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

MODULE = 'softwareservices_timesheets_connector'
# language -> (label of service_url, "Pairing code" field, message of action_pair without a code, label of a menu, the Unpair button)
EXPECTED = {
    'nl_BE': ('Service-URL', 'Koppelcode', 'Plak de koppelcode van 12 tekens uit', 'Gekoppelde werknemers', 'Ontkoppelen'),
    'fr_BE': ('URL du service', 'Code de couplage', 'Collez le code de couplage de 12 caractères', 'Employés liés', 'Découpler'),
    'de_DE': ('Dienst-URL', 'Kopplungscode', 'Fügen Sie den 12-stelligen Kopplungscode', 'Verknüpfte Mitarbeiter', 'Entkoppeln'),
}


@tagged('post_install', '-at_install')
class TestTranslations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for lang in EXPECTED:
            cls.env['res.lang']._activate_lang(lang)
        cls.env['ir.module.module']._load_module_terms([MODULE], list(EXPECTED), overwrite=True)

    def test_english_is_the_source(self):
        env = self.env(context={'lang': 'en_US'})
        fields = env['ss_timesheets.connection'].fields_get(['service_url', 'pair_code'])
        self.assertEqual((fields['service_url']['string'], fields['pair_code']['string']), ('Service URL', 'Pairing code'))
        with self.assertRaisesRegex(UserError, 'Paste the 12-character pairing code from'):
            env['ss_timesheets.connection'].get_connection().write({'pair_code': ''})
            env['ss_timesheets.connection'].get_connection().action_pair()

    def test_translated(self):
        for lang, (service_url, pair_code, message, menu, unpair) in EXPECTED.items():
            with self.subTest(lang=lang):
                env = self.env(context={'lang': lang})
                fields = env['ss_timesheets.connection'].fields_get(['service_url', 'pair_code'])
                self.assertEqual((fields['service_url']['string'], fields['pair_code']['string']), (service_url, pair_code))
                with self.assertRaisesRegex(UserError, message):
                    env['ss_timesheets.connection'].get_connection().write({'pair_code': ''})
                    env['ss_timesheets.connection'].get_connection().action_pair()
                self.assertEqual(env.ref(f'{MODULE}.menu_employee_links').name, menu)
                arch = env['ss_timesheets.connection'].get_view(env.ref(f'{MODULE}.view_connection_form').id)['arch']
                self.assertIn(f'string="{unpair}"', arch)                            # a button of the form view
