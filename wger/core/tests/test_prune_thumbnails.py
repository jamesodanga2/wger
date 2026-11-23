# This file is part of wger Workout Manager.
#
# wger Workout Manager is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# wger Workout Manager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License

# Standard Library
import importlib
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import (
    SimpleTestCase,
    override_settings,
)

# wger
from wger.exercises.models.image import exercise_image_upload_dir
from wger.gallery.models.image import gallery_upload_dir
from wger.nutrition.models.image import ingredient_image_upload_dir


# The module name contains a hyphen, so it can't be imported with a regular statement
prune_thumbnails = importlib.import_module('wger.core.management.commands.prune-thumbnails')

INGREDIENT_ORIGINAL = 'ingredients/7a/b1/7ab1c2d3-0000-4000-8000-000000000001.jpg'
GALLERY_ORIGINAL = 'gallery/1/9c9d0e0f-0000-4000-8000-000000000002.png'

# Thumbnails that match the default aliases: small (200x200) and medium (400x400)
CURRENT_THUMBNAILS = [
    f'{INGREDIENT_ORIGINAL}.200x200_q85.jpg',
    f'{INGREDIENT_ORIGINAL}.400x400_q85.jpg',
    # Older easy-thumbnails versions did not write the quality token
    f'{INGREDIENT_ORIGINAL}.200x200.jpg',
    f'{GALLERY_ORIGINAL}.400x400_q85.png',
]

# Thumbnails that no configured alias produces
STALE_INGREDIENT_THUMBNAILS = [
    f'{INGREDIENT_ORIGINAL}.150x150_q85.jpg',  # size no longer configured
    f'{INGREDIENT_ORIGINAL}.200x200_q70.jpg',  # quality differs from the alias
    f'{INGREDIENT_ORIGINAL}.200x200_q85_crop-smart.jpg',  # alias is not cropped
]
STALE_GALLERY_THUMBNAILS = [
    f'{GALLERY_ORIGINAL}.1024x1024_q85.png',
]

DEFAULT_ALIASES = {
    '': {
        'small': {'size': (200, 200)},
        'medium': {'size': (400, 400)},
    },
}

# Django resets default_storage whenever STORAGES is overridden, so overriding it
# in setUp gives every test a fresh, empty InMemoryStorage
ISOLATED_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(THUMBNAIL_ALIASES=DEFAULT_ALIASES)
class PruneThumbnailsCommandTestCase(SimpleTestCase):
    """
    Tests for `manage.py prune-thumbnails`, which deletes thumbnails whose alias
    is no longer configured in THUMBNAIL_ALIASES
    """

    ORIGINALS = [INGREDIENT_ORIGINAL, GALLERY_ORIGINAL]
    ALL_STALE = STALE_INGREDIENT_THUMBNAILS + STALE_GALLERY_THUMBNAILS

    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(STORAGES=ISOLATED_STORAGES))
        for name in self.ORIGINALS + CURRENT_THUMBNAILS + self.ALL_STALE:
            saved_name = default_storage.save(name, ContentFile(b'image bytes'))
            # Guard against the storage renaming a file because it already exists
            self.assertEqual(saved_name, name)

    def run_command(self, *args):
        out = StringIO()
        err = StringIO()
        call_command('prune-thumbnails', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def assert_exist(self, names):
        for name in names:
            self.assertTrue(default_storage.exists(name), f'{name} should still exist')

    def assert_deleted(self, names):
        for name in names:
            self.assertFalse(default_storage.exists(name), f'{name} should be deleted')

    def test_dry_run_deletes_nothing_and_reports_counts(self):
        """Without --delete, the command only reports what it would remove"""
        out, _ = self.run_command()

        self.assert_exist(self.ORIGINALS + CURRENT_THUMBNAILS + self.ALL_STALE)
        self.assertIn('Mode: dry-run', out)
        self.assertIn('Would delete: 4 files', out)
        self.assertRegex(out, r'Kept:\s+4 thumbnails matching current aliases')
        self.assertIn('Re-run with --delete to remove these files.', out)

    def test_delete_removes_stale_thumbnails_and_keeps_current_ones(self):
        """
        With --delete, thumbnails of a configured size are kept (with, without, or
        with the default quality token) and every other thumbnail is removed
        """
        out, err = self.run_command('--delete')

        self.assert_exist(CURRENT_THUMBNAILS)
        self.assert_deleted(self.ALL_STALE)
        self.assertIn('Mode: DELETE', out)
        self.assertIn('Deleted: 4 files', out)
        self.assertRegex(out, r'Kept:\s+4 thumbnails matching current aliases')
        self.assertEqual(err, '')

    def test_delete_never_removes_originals(self):
        """Original uploads survive, even when no alias is configured at all"""
        with self.settings(THUMBNAIL_ALIASES={'': {}}):
            self.run_command('--delete')

        self.assert_exist(self.ORIGINALS)
        self.assert_deleted(CURRENT_THUMBNAILS + self.ALL_STALE)

    def test_path_limits_the_scan_to_a_subdirectory(self):
        """--path only touches files below the given prefix"""
        out, _ = self.run_command('--delete', '--path', 'ingredients')

        self.assert_deleted(STALE_INGREDIENT_THUMBNAILS)
        self.assert_exist(STALE_GALLERY_THUMBNAILS)
        self.assertIn('Scanning: ingredients', out)
        self.assertIn('Deleted: 3 files', out)

    def test_cropped_alias_keeps_cropped_and_removes_uncropped_thumbnails(self):
        """For a cropped alias, only thumbnails with the matching crop are current"""
        cropped_aliases = {'': {'small': {'size': (200, 200), 'crop': 'smart'}}}

        with self.settings(THUMBNAIL_ALIASES=cropped_aliases):
            self.run_command('--delete')

        self.assert_exist([f'{INGREDIENT_ORIGINAL}.200x200_q85_crop-smart.jpg'])
        self.assert_deleted(
            [
                f'{INGREDIENT_ORIGINAL}.200x200_q85.jpg',
                f'{INGREDIENT_ORIGINAL}.200x200.jpg',
            ]
        )

    def test_failed_delete_is_reported_and_does_not_abort_the_run(self):
        """A storage error on one file is written to stderr and the scan continues"""
        with patch(
            'django.core.files.storage.InMemoryStorage.delete',
            side_effect=OSError('permission denied'),
        ) as mock_delete:
            out, err = self.run_command('--delete')

        # Every stale thumbnail was attempted, not just the first one
        self.assertEqual(mock_delete.call_count, len(self.ALL_STALE))
        for name in self.ALL_STALE:
            self.assertIn(f'Failed to delete {name}: permission denied', err)
        # The run reaches its summary; the deleted count is not asserted (see #11)
        self.assertRegex(out, r'Kept:\s+4 thumbnails matching current aliases')


class UploadPathsAreNeverThumbnailNamesTestCase(SimpleTestCase):
    """
    Upload paths never end in a thumbnail suffix (`.<W>x<H>[_q<Q>][_crop].<ext>`),
    whatever name the user uploads, so prune-thumbnails cannot delete originals
    """

    # A user-supplied file name that looks exactly like a thumbnail
    HOSTILE_FILENAME = 'holiday.200x200_q85.jpg'

    def assert_not_a_thumbnail_name(self, path):
        basename = path.rsplit('/', 1)[-1]
        self.assertIsNone(
            prune_thumbnails.THUMBNAIL_SUFFIX_RE.search(basename),
            f'{path} would be treated as a thumbnail and deleted',
        )

    def test_gallery_upload_path(self):
        instance = SimpleNamespace(user=User(id=1))
        self.assert_not_a_thumbnail_name(gallery_upload_dir(instance, self.HOSTILE_FILENAME))

    def test_ingredient_image_upload_path(self):
        instance = SimpleNamespace(uuid='7ab1c2d3-0000-4000-8000-000000000001')
        self.assert_not_a_thumbnail_name(
            ingredient_image_upload_dir(instance, self.HOSTILE_FILENAME)
        )

    def test_exercise_image_upload_path(self):
        instance = SimpleNamespace(
            exercise=SimpleNamespace(id=1),
            uuid='7ab1c2d3-0000-4000-8000-000000000003',
        )
        self.assert_not_a_thumbnail_name(exercise_image_upload_dir(instance, self.HOSTILE_FILENAME))

    def test_thumbnail_pattern_does_match_the_hostile_filename(self):
        """Sanity check, so the tests above can't pass because the regex matches nothing"""
        self.assertIsNotNone(prune_thumbnails.THUMBNAIL_SUFFIX_RE.search(self.HOSTILE_FILENAME))
