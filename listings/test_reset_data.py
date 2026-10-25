from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import CommandError
from django.test import SimpleTestCase

from listings.management.commands.reset_data import Command


class ResetDataCommandTests(SimpleTestCase):
    def setUp(self):
        self.database = Mock()
        self.database.settings_dict = {"NAME": "easy_rent"}
        self.connections = Mock()
        self.connections.databases = {"default": {}}
        self.connections.__getitem__ = Mock(return_value=self.database)
        self.command = Command(stdout=StringIO())

    @patch("listings.management.commands.reset_data.call_command")
    @patch("listings.management.commands.reset_data.connections")
    def test_requires_matching_database_confirmation(self, connections, call_command):
        connections.databases = self.connections.databases
        connections.__getitem__ = self.connections.__getitem__

        with self.assertRaisesMessage(CommandError, "no data was deleted"):
            self.command.handle(
                database="default",
                confirm_database="wrong_database",
                verbosity=1,
            )

        call_command.assert_not_called()

    @patch("listings.management.commands.reset_data.call_command")
    @patch("listings.management.commands.reset_data.connections")
    def test_flushes_confirmed_database_without_prompting(self, connections, call_command):
        connections.databases = self.connections.databases
        connections.__getitem__ = self.connections.__getitem__

        self.command.handle(
            database="default",
            confirm_database="easy_rent",
            verbosity=1,
        )

        call_command.assert_called_once_with(
            "flush",
            database="default",
            interactive=False,
            verbosity=1,
        )

    @patch("listings.management.commands.reset_data.connections")
    def test_rejects_unknown_database_alias(self, connections):
        connections.databases = {}

        with self.assertRaisesMessage(CommandError, "Unknown database alias"):
            self.command.handle(
                database="missing",
                confirm_database="easy_rent",
                verbosity=1,
            )
