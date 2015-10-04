#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
main.py -- Udacity conference server-side Python App Engine
    HTTP controller handlers for memcache & task queue access

$Id$

created by wesc on 2014 may 24

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

import webapp2
from google.appengine.api import app_identity
from google.appengine.api import mail
from conference import ConferenceApi


class SetAnnouncementHandler(webapp2.RequestHandler):

    def get(self):
        """Set Announcement in Memcache."""

        ConferenceApi._cacheAnnouncement()
        self.response.set_status(204)


class FeaturedSpeakersHandler(webapp2.RequestHandler):

    def post(self):
        """Build List of Featured Speaker by Conference"""

        wsck = self.request.get('websafeConferenceKey')
        if wsck:
            ConferenceApi._featuredSpeakers(wsck)
        else:
            ConferenceApi._featuredSpeakers()


# TODO Add a task

class RankSessionsHandler(webapp2.RequestHandler):

    def post(self):
        """Order top sessions by conference"""

        wsck = self.request.get('websafeConferenceKey')
        if wsck:
            ConferenceApi._rankSessions(wsck)
        else:
            ConferenceApi._rankSessions()


class SendConfirmationEmailHandler(webapp2.RequestHandler):

    def post(self):
        """Send email confirming Conference creation."""

        mail.send_mail('noreply@%s.appspotmail.com'
                       % app_identity.get_application_id(),
                       self.request.get('email'),
                       'You created a new Conference!',
                       '''Hi, you have created a following conference:\r
\r
%s'''
                       % self.request.get('conferenceInfo'))  # from
                                                              # to
                                                              # subj
                                                              # body


app = webapp2.WSGIApplication([('/crons/set_announcement',
                              SetAnnouncementHandler),
                              ('/tasks/rank_sessions',
                              RankSessionsHandler),
                              ('/tasks/featured_speakers',
                              FeaturedSpeakersHandler),
                              ('/tasks/send_confirmation_email',
                              SendConfirmationEmailHandler)],
                              debug=True)
