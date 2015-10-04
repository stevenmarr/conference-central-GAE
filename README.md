App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool

Task 1:
Session and speaker implementation
There are two models to support sessions, a ndb.Model for sessions in the data store and a messages.Message form to support endpoint data transfer via Google ProtoRPC.   

Session objects contain the following fields:
    name            = ndb.StringProperty(required=True)
    highlights      = ndb.StringProperty()
    speaker         = ndb.StringProperty(required=True)
    duration        = ndb.IntegerProperty()
    typeOfSession   = ndb.StringProperty(repeated=True)
    date            = ndb.DateProperty()
    start_time      = ndb.TimeProperty()
    wish_list_count = ndb.IntegerProperty()
New session objects are children of the conference they are created from, and the method for creation includes default values for fields left blank form user creation. Session.name is a required field and is does not have an default value if left blank, it is required for creation and to ensure uniqueness in the data store key.  Session.highlights is an optional string field, its blank by default and allows for session description.  Session.speaker is a required field, if left blank on creation it's assigned 'unassigned'.  Session.duration is the number of minutes each session will last, its an integer to allow for operations with datetime.time() objects.  Session.typeOfSession is a list of session tags.  Session.date and Session.startime are datetime objects that indicate day and time of sessions.  Session.wish_list_count is an integer field used for ranking sessions based on their popularity.

The SessionForm object contains the following fields for creation and later output:
	name            = messages.StringField(1)
    highlights      = messages.StringField(2)
    speaker         = messages.StringField(3)
    duration        = messages.IntegerField(4)
    typeOfSession   = messages.StringField(5, repeated=True)
    date            = messages.StringField(6)
    start_time      = messages.StringField(7)
    websafeConferenceKey      = messages.StringField(8)
    websafeKey      = messages.StringField(9)

 SessionForm fields follow the same logic as the Session fields with the exception of: SessionForm.date and SessionForm.start_time are type StringField and the data past to and from is converted using the datetime.date() and datetime.time() methods.SessionForm also contains fields for websafeConferenceKey and websafeKey to allow conference and session information to be past via api calls.  SessionForm does not contain a wish_list_count field since incrementing and decrementing this value are handled by api calls not related to session creation or session reads.

The createSession endpoint calls the method _createSessionObject, _createSessionObject requests the websafeConferenceKey from the http request and verifies a user is logged in.  Next _getConferenceByKey() is called with the websafeConferenceKey as an argument to retrieve the conference model, a test is done to make sure the current user is the user who created the conference or raise an exception.  Next we request the name field, if name was not submitted we raise and exception, next we check that name is unique in the conference sessions.  Now that we have verified we have a conference the user is authorized to add sessions to and a present and unique name for the session exists we move forward with creating a new Session object.  
In creating the session object we delete the request fields that are not needed for a session object, websafeConferenceKey and websafeKey. Next we run a loop across the remaining request fields and match the values from SessionForm to Session.  The fields data and start_time are special cases since they come from the request as unicode values but must be stored as datetime.date() and datetime.time() objects respectively.  To accomplish this we run the unicode requests through datetime formatting the retrieve the correct objects for storage. Next we initialize the wish_list_count to zero, extract the conference key and build a new key for the session using the conference key as the parent. Next the session is stored and we add a task to featured_speakers to update the featured_speakers cache.

Task 3
Additional Queries:
Two additional queries have been implemented in this code, getTopSessions and getConferenceSessionsByDate.  
getTopSessions
The api call getTopSessions pulls the Session query from memcache and returns a dict of the top five sessions for a given conference ranked by the current number of wish list entries the session has, if the memcache is stale or does not exist a new task is sent to /tasks/rank_sessions and an exception is raised.  

The call to /tasks/rank_sessions calls on _rankSessions() passing in the conference key, this method checks the validity of the conference key and raises an exception if the key is invalid.  Next a query is generated to on all conference sessions ordering by their wish_list_count and returning the first five.  This query is then stored in memcache.

Each time a session is added to a wish list a task call to /tasks/rank_sessions is made to keep the cache accurate.

getConferenceSessionsByDate
The api call getConferenceSessionsByDate takes in the conference key as and argument and returns a dict off all the conference sessions organized by date.  This is accomplished by, checking conference key validity and then running a query on Sessions using conference key as an ancestor and ordering the results by start_date.


getNonWorkshopBefore7PM
The basic problem with this query is that it require filtering a query on two conditionals (typeOfSession NOT 'workshop') AND (start_time < 7:00PM).  ndb datastore does not support queries of this type.  In this solution we query the sessions using the conference key as the ancestor and filtering the results for sessions to exclude workshop sessions.  We then iterate through each session in the query and add sessions with a start_time earlier than 7:00PM to the items list.  Finally the items list is returned.
 
