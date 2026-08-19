from importlib import import_module

import django
from django.db import connection
from django.test import TestCase
from django.utils import unittest


@unittest.skipUnless(django.VERSION >= (1, 7), 'Django migrations require 1.7')
class AuthTokenMigrationTests(TestCase):

    def test_native_migration_is_visible_with_user_dependency(self):
        from django.conf import settings
        from django.db import migrations
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)

        migration = loader.disk_migrations.get(('authtoken', '0001_initial'))

        self.assertIsNotNone(migration)
        self.assertIn(
            migrations.swappable_dependency(settings.AUTH_USER_MODEL),
            migration.dependencies,
        )

    @unittest.skipIf(django.VERSION >= (1, 8), 'South is incompatible with 1.8')
    def test_south_migration_remains_separately_importable(self):
        from south.v2 import SchemaMigration

        try:
            migration_module = import_module(
                'rest_framework.authtoken.south_migrations.0001_initial'
            )
        except ImportError:
            self.fail('The preserved South migration could not be imported')

        self.assertTrue(issubclass(migration_module.Migration, SchemaMigration))

    def test_native_migration_creates_token_schema_and_history(self):
        from django.contrib.auth import get_user_model
        from django.db.migrations.recorder import MigrationRecorder
        from rest_framework.authtoken.models import Token

        table_name = Token._meta.db_table
        user_table_name = get_user_model()._meta.db_table

        self.assertTrue(
            MigrationRecorder.Migration.objects.filter(
                app='authtoken',
                name='0001_initial',
            ).exists()
        )

        cursor = connection.cursor()
        description = connection.introspection.get_table_description(
            cursor,
            table_name,
        )
        column_names = [column[0] for column in description]
        constraints = connection.introspection.get_constraints(cursor, table_name)

        self.assertTrue(any(
            constraint['primary_key'] and constraint['columns'] == ['key']
            for constraint in constraints.values()
        ))
        self.assertTrue(any(
            constraint['unique'] and constraint['columns'] == ['user_id']
            for constraint in constraints.values()
        ))

        foreign_keys = [
            constraint['foreign_key']
            for constraint in constraints.values()
            if constraint.get('foreign_key')
        ]
        if foreign_keys:
            self.assertIn((user_table_name, 'id'), foreign_keys)
        else:
            relations = connection.introspection.get_relations(cursor, table_name)
            relation = relations.get('user_id')
            if relation is None:
                user_column_index = column_names.index('user_id')
                relation = relations[user_column_index]
            self.assertEqual(relation[1], user_table_name)

        if connection.vendor == 'postgresql':
            cursor.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = %s AND indexdef LIKE %s",
                [table_name, '%varchar_pattern_ops%'],
            )
            self.assertTrue(cursor.fetchall())

    def test_token_round_trip_preserves_schema_contract(self):
        from django.contrib.auth import get_user_model
        from django.db import IntegrityError, transaction
        from rest_framework.authtoken.models import Token

        user_model = get_user_model()
        user = user_model.objects.create_user('migration-user')

        token = Token.objects.create(user=user)
        reloaded = Token.objects.get(key=token.key)

        self.assertEqual(reloaded.user, user)
        self.assertEqual(len(reloaded.key), 40)
        int(reloaded.key, 16)
        self.assertIsNotNone(reloaded.created)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Token.objects.create(user=user)
