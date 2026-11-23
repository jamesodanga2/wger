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
import datetime

# Django
from django.contrib.auth.models import User
from django.urls import reverse

# Third Party
from icalendar import Calendar

# wger
from wger.core.tests.base_testcase import WgerTestCase
from wger.manager.models import (
    Day,
    Routine,
)


class WorkoutICalExportTestCase(WgerTestCase):
    """
    Tests exporting a routine as an iCal file.

    The routine built in setUp has a fixed, past date range and days that do not
    need logs to advance, so its date sequence does not depend on the current date:

        Mon 2024-03-04  Push
        Tue 2024-03-05  (rest)
        Wed 2024-03-06  Pull
        Thu 2024-03-07  Push
        Fri 2024-03-08  (rest)
        Sat 2024-03-09  Pull
    """

    EXPECTED_EVENTS = [
        (datetime.date(2024, 3, 4), 'Push', 'Chest and shoulders'),
        (datetime.date(2024, 3, 6), 'Pull', 'Back and biceps'),
        (datetime.date(2024, 3, 7), 'Push', 'Chest and shoulders'),
        (datetime.date(2024, 3, 9), 'Pull', 'Back and biceps'),
    ]

    def setUp(self):
        super().setUp()
        self.routine = Routine.objects.create(
            user=User.objects.get(username='test'),
            name='iCal test routine',
            start=datetime.date(2024, 3, 4),
            end=datetime.date(2024, 3, 9),
        )
        Day.objects.create(
            routine=self.routine,
            order=1,
            name='Push',
            description='Chest and shoulders',
        )
        Day.objects.create(
            routine=self.routine,
            order=2,
            name='Rest day',
            description='Recovery',
            is_rest=True,
        )
        Day.objects.create(
            routine=self.routine,
            order=3,
            name='Pull',
            description='Back and biceps',
        )

    def export_ical(self, pk):
        return self.client.get(reverse('manager:routine:ical', kwargs={'pk': pk}))

    def get_events(self, response):
        """Parse the response body and return its VEVENT components"""
        calendar = Calendar.from_ical(response.content)
        return [component for component in calendar.walk('VEVENT')]

    def test_export_ical_anonymous(self):
        """An anonymous user cannot export a routine"""
        response = self.export_ical(self.routine.pk)

        self.assertIn(response.status_code, (403, 404))

    def test_export_ical_other(self):
        """A logged-in user cannot export a routine owned by somebody else"""
        self.user_login('admin')

        response = self.export_ical(self.routine.pk)

        self.assertIn(response.status_code, (403, 404))

    def test_export_ical_owner(self):
        """The owner gets a calendar file"""
        self.user_login('test')

        response = self.export_ical(self.routine.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/calendar')

    def test_export_has_one_event_per_training_day_with_date_and_name(self):
        """
        Given a routine with training and rest days, the export contains exactly one
        all-day event per training day, on the right date, named after the day
        """
        self.user_login('test')

        events = self.get_events(self.export_ical(self.routine.pk))

        actual = [
            (event.decoded('DTSTART'), str(event['SUMMARY']), str(event['DESCRIPTION']))
            for event in events
        ]
        self.assertEqual(sorted(actual), self.EXPECTED_EVENTS)

    def test_export_matches_the_routine_date_sequence(self):
        """
        The exported dates are the non-rest days of the routine's date sequence, the
        same source the rest of the application uses for its calendar
        """
        self.user_login('test')

        events = self.get_events(self.export_ical(self.routine.pk))

        expected_dates = [
            day_data.date
            for day_data in self.routine.date_sequence
            if day_data.day is not None and not day_data.day.is_rest
        ]
        self.assertEqual(sorted(event.decoded('DTSTART') for event in events), expected_dates)

    def test_export_skips_rest_days(self):
        """Rest days never show up in the user's calendar"""
        self.user_login('test')

        events = self.get_events(self.export_ical(self.routine.pk))

        exported_dates = {event.decoded('DTSTART') for event in events}
        self.assertNotIn(datetime.date(2024, 3, 5), exported_dates)
        self.assertNotIn(datetime.date(2024, 3, 8), exported_dates)
        self.assertNotIn('Rest day', {str(event['SUMMARY']) for event in events})

    def test_export_events_have_unique_uids(self):
        """Every event has a UID, and no two events in one export share one"""
        self.user_login('test')

        events = self.get_events(self.export_ical(self.routine.pk))

        uids = [str(event['UID']) for event in events]
        self.assertEqual(len(uids), len(self.EXPECTED_EVENTS))
        self.assertTrue(all(uids))
        self.assertEqual(len(set(uids)), len(uids))

    def test_export_routine_without_days_has_no_events(self):
        """A routine without any days exports a valid, empty calendar"""
        self.user_login('test')
        empty_routine = Routine.objects.get(pk=3)
        self.assertEqual(empty_routine.user.username, 'test')
        self.assertFalse(empty_routine.days.exists())

        response = self.export_ical(empty_routine.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_events(response), [])
