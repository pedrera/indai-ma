import unittest
from unittest.mock import Mock

from streamlit.testing.v1 import AppTest


def _render_portfolio_with_agent(callback):
    from industrial_gases.portfolio_ui import render_supply_portfolio
    render_supply_portfolio(on_run_supply_agent=callback)


def _render_portfolio_read_only():
    from industrial_gases.portfolio_ui import render_supply_portfolio
    render_supply_portfolio()


class SupplyAgentUITests(unittest.TestCase):
    def test_supply_agent_is_embedded_in_portfolio_and_dispatches_selected_position(self):
        callback = Mock()

        app = AppTest.from_function(_render_portfolio_with_agent, args=(callback,)).run()
        self.assertFalse(app.exception)
        self.assertIn("Supply Agent", "\n".join(item.value for item in app.subheader))
        self.assertIn("Position for analysis", [item.label for item in app.selectbox])
        self.assertIn("Question about this position", [item.label for item in app.text_area])
        app.selectbox(key="supply_agent_item_id").set_value("alimentos-sur-malaga-n2")
        app.text_area(key="supply_agent_question").set_value("¿Qué dice el procedimiento de N2?")
        app.button(key="supply_agent_start").click().run()
        self.assertFalse(app.exception)
        callback.assert_called_once()
        portfolio, attention, item_id, question = callback.call_args.args
        self.assertEqual(item_id, "alimentos-sur-malaga-n2")
        self.assertEqual(question, "¿Qué dice el procedimiento de N2?")
        self.assertEqual(portfolio.items[-1].item_id, item_id)
        self.assertEqual(attention.items[-1].item_id, item_id)

    def test_portfolio_without_agent_dispatch_remains_read_only(self):
        app = AppTest.from_function(_render_portfolio_read_only).run()
        self.assertFalse(app.exception)
        self.assertIn("Supply Agent", "\n".join(item.value for item in app.subheader))
        self.assertTrue(app.button(key="supply_agent_start").disabled)


if __name__ == "__main__":
    unittest.main()
