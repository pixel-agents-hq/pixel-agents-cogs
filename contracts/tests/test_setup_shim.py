"""Guards contracts/__init__.py's no-op `setup` -- see its docstring for why
it exists. Losing it silently reintroduces the "Failed to reload cogs:
contracts" error dev-time hot reload tooling shows in Discord, so this is
worth a direct test rather than relying on someone noticing in practice.
"""

from __future__ import annotations

import unittest

import contracts


class TestSetupShim(unittest.IsolatedAsyncioTestCase):
    async def test_setup_exists_and_is_a_harmless_no_op(self) -> None:
        self.assertTrue(callable(contracts.setup))

        result = await contracts.setup(object())

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
