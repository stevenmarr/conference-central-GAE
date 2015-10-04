#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

api calls added by Steven Marr 2015 Sep 6

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import logging
import httplib
import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import SessionForm
from models import SessionForms
from models import Session
from models import FeaturedSpeakerForm
from models import FeaturedSpeakerForms


from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

S_DEFAULTS = {
    "highlights": "None",   
    "speaker": "unassigned",      
    "duration": 15,
    "typeOfSession":  [ "Default", "Topic" ],
    "start_time": '08:00'
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

SESSION_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SESSION_SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
)

# - - - - - - - - - 
def getConferenceByKey(websafeConferenceKey):
    "given a websafeConferenceKey return the conference"
    try:
        conf = ndb.Key(urlsafe=websafeConferenceKey).get()
    except:
        raise endpoints.NotFoundException(
           'No conference found with key: %s' % websafeConferenceKey)
    return conf
#- - - - - - - - - - - - - - - - - - - - -

class UnProcessableException(endpoints.ServiceException):
  """UnProcessable exception that is mapped to a 412 response."""
  http_status = httplib.PRECONDITION_FAILED



@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request

    


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = getConferenceByKey(request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")

# - - - Sessions - - - - - - - - - - - - - - - - - - - - - 
    

    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert Date to date string;
                if field.name == 'date':
                    setattr(sf, field.name, str(getattr(session, field.name)))
                # convert start_time to time string;
                elif field.name == 'start_time':
                    setattr(sf, field.name, str(getattr(session, field.name)))
                # convert websafeKey to urlsafe value;
                elif field.name == "websafeKey":
                    setattr(sf, field.name, session.key.urlsafe())
                # copy other fields
                else:
                    setattr(sf, field.name, getattr(session, field.name))
        #Checks that all required fields are initialized. 
        #Raises a ValidationError if the Message object is not initialized.
        sf.check_initialized() 
        return sf
    
    def _createSessionObject(self, request):
        """Create or update Session object, returning request."""
        wsck = request.websafeConferenceKey

        # preload necessary data items
        conf = getConferenceByKey(wsck)
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        
        # check in current user is authorized to add sessions
        if conf.organizerUserId != user_id:
            raise endpoints.ForbiddenException('Unauthorized to create sessions for %s conference' % conf.name)
        # check if name exists
        if not request.name:
            raise endpoints.UnProcessableException("Session 'name' field required")
        # check if name is unique
        if Session.query(ancestor=conf.key).filter(Session.name == request.name).get():
            raise endpoints.BadRequestException("Duplicate session name already exists")


        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey']
        del data['websafeKey']

        # add default values for those missing (both data model & outbound Message)
        for df in S_DEFAULTS:
            if data[df] in (None, []):
                data[df] = S_DEFAULTS[df]
                setattr(request, df, S_DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['date']:
            try:
                data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
            except:
                raise UnProcessableException('Incorrect date format or data')
        # initialize default value if date was not provided         
        else: data['date'] = conf.startDate
        # convert time from string to time object if properly formated
        if data['start_time']:
            try:
                data['start_time'] = datetime.strptime(data['start_time'][:5], "%H:%M").time()
            except:
                 raise UnProcessableException('Incorrect time format or data')
        
        data['wish_list_count'] = 0
        c_key = conf.key
        s_id = Session.allocate_ids(size=1, parent=c_key)[0]
        s_key = ndb.Key(Session, s_id, parent=c_key)
        data['key'] = s_key
        
        # create Session
        Session(**data).put()
        
        # update featured speaker list
        taskqueue.add(params={'websafeConferenceKey': wsck},
            url='/tasks/featured_speakers')

        return request

    @staticmethod
    def _rankSessions(websafeConferenceKey):
        conf = getConferenceByKey(websafeConferenceKey)
        sessions = Session.query(ancestor=conf.key).order(Session.wish_list_count).fetch(5)
        memcache.set('TOP_SESSIONS_FOR_%s' % conf.name, sessions)
        return sessions

    @staticmethod
    def _featuredSpeakers(websafeConferenceKey):
        sas = {} #SpeakersAndSessions
        conf = getConferenceByKey(websafeConferenceKey)
        sessions = Session.query(ancestor=conf.key)
        for session in sessions:
            if sas.has_key(session.speaker):
                sas[session.speaker].append(session.name)
            else:
                sas[session.speaker]=[session.name]
        keys = sas.keys()
        for key in keys: #remove speakers from list if they have less than two sessions
            if len(sas[key]) < 2:
                del sas[key]
        memcache.set('FEATURED_SPEAKERS_FOR_%s' % conf.name, sas)
        return sas


    def _copyFeaturedSpeakersToForm(self, speaker, sessions):
        """Copy relevant fields from Session to FeatureSpeakerForm."""
        fsf = FeaturedSpeakerForm()
        fsf.speaker = speaker
        for session in sessions:
            fsf.name.append(session)
        fsf.check_initialized()
        return fsf
        
#TODO getConferenceSessions
    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='get_sessions/{websafeConferenceKey}',
            http_method='GET', name='getSessions')
    def getConferenceSessions(self, request):
        """Return sessions for conference."""
        conf = getConferenceByKey(request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf.key)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

#TODO getConferenceSessionsByType
    @endpoints.method(SESSION_TYPE_GET_REQUEST, SessionForms,
            path='get_sessions_by_type/{websafeConferenceKey}/{typeOfSession}',
            http_method='GET',
            name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return all conference sessions of specified type."""
        conf = getConferenceByKey(request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf.key).filter(Session.typeOfSession == request.typeOfSession)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

#TODO Create addtional queries
    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='conf_sessions_by_popularity/{websafeConferenceKey}',
            http_method='GET',
            name='getTopSessions')
    def getTopSessions(self, request):
        """Return top five conference sessions based on the number of adds to wish lists."""
        wsck = request.websafeConferenceKey
        conf = getConferenceByKey(wsck)
        sessions = memcache.get('TOP_SESSIONS_FOR_%s' % conf.name)
        if not sessions:
            taskqueue.add(params={'websafeConferenceKey': wsck},
            url='/tasks/rank_sessions')
            sessions = ConferenceApi._rankSessions(wsck)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='get_sessions_by_date/{websafeConferenceKey}',
            http_method='GET',
            name='getConferenceSessionsByDate')
    def getConferenceSessionsByDate(self, request):
        """Given a conference key, return all the sessions organized by session date and time."""
        conf = getConferenceByKey(request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf.key).order(Session.date).order(Session.start_time)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

#TODO createSession
    @endpoints.method(SessionForm, SessionForm, path='session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

#TODO getSessionsBySpeaker 
    @endpoints.method(SESSION_SPEAKER_GET_REQUEST, SessionForms,
            path='get_sessions_by_speaker/{speaker}',
            http_method='GET',
            name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return sessions across all conferences with specified speaker."""
        sessions = Session.query().filter(Session.speaker == request.speaker)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

#TODO addSessionToWishlist
    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
            path='add_session_to_wishlist/{websafeSessionKey}',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add session to user's wish list"""
        return self._sessionToWishlist(request)

    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
            path='remove_session_from_wishlist/{websafeSessionKey}',
            http_method='POST', name='deleteSessionFromWishlist')
    def deleteSessionFromWishlist(self, request):
        """Delete session from user's wish list"""
        return self._sessionToWishlist(request, add=False)  

#TODO getSessionsInWishlist
    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='get_wish_list_sessions',
            http_method='GET',
            name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Return sessions in users wishlist"""
        prof = self._getProfileFromUser() # get user Profile
        sessions = [ndb.Key(urlsafe=sk).get() for sk in prof.sessionKeysInWishlist]
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

#TODO getFeaturedSpeaker
    @endpoints.method(CONF_GET_REQUEST, FeaturedSpeakerForms,
            path = 'get_featured_speaker/{websafeConferenceKey}',
            http_method='GET',
            name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """For a given conference return the featured speakers, those speaking twice"""
        wsck = request.websafeConferenceKey
        conf = getConferenceByKey(wsck)
        fspeakers = memcache.get('FEATURED_SPEAKERS_FOR_%s' % conf.name)
        if not fspeakers:
            taskqueue.add(params={'websafeConferenceKey': wsck},
            url='/tasks/featured_speakers')
            fspeakers = self._featuredSpeakers(wsck)
            #raise endpoints.NotFoundException(
            #    'Featured speaker list is temporarily unavailable, please try again later')
        
        return FeaturedSpeakerForms(
            items=[self._copyFeaturedSpeakersToForm(key, fspeakers[key]) for key in fspeakers.keys()])
        

#TODO Solve the following query related problem
    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='non_workshops_before_7PM/{websafeConferenceKey}',
            http_method='GET',
            name='getNonWorkshopBefore7PM')
    def getNonWorkshopBefore7PM(self, request):
        """Return non workshop sessions before 7PM"""
        conf = getConferenceByKey(request.websafeConferenceKey)
        sessions = Session.query(ancestor = conf.key).filter(Session.typeOfSession != 'Workshop')
        items = []
        for session in sessions:
            if session.start_time.hour < 19:
                items.append(self._copySessionToForm(session))
        return SessionForms(items=items)    
    

# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = getConferenceByKey(wsck)
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    def _sessionToWishlist(self, request, add=True):
        """Add or remove sessions from users wishlist."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile
        # check if session exists given websafeSessionKey
        # get conference; check that it exists
        wssk = request.websafeSessionKey
        
        #conf = getConferenceByKey(wsck)
        
        try:
            session = ndb.Key(urlsafe=wssk).get()
        except:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % wssk)
        wsck = session.key.parent().urlsafe()
       
        if not session:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % wssk)
            
        # register
        if add:
            # check if session is already in wish list
            if wssk in prof.sessionKeysInWishlist:
                raise ConflictException(
                    "You have already added this session to your wishlist")
                
            prof.sessionKeysInWishlist.append(wssk)
            session.wish_list_count += 1
            retval = True
        # unregister
        else:
            if wssk in prof.sessionKeysInWishlist:
                prof.sessionKeysInWishlist.remove(wssk)
                session.wish_list_count -= 1
                retval = True
            else:
                retval = False
                raise ConflictException(
                    "This session does not exist in your wishlist")
        # write things back to the datastore & return
        prof.put()
        session.put()
        
        taskqueue.add(params={'websafeConferenceKey': wsck},
            url='/tasks/rank_sessions')
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

  


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )


api = endpoints.api_server([ConferenceApi]) # register API
