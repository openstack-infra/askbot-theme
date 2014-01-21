import settings
import pickle
from askbot import models
from askbot.conf import settings as askbot_settings
from askbot.utils.console import ProgressBar
from askbot.utils.slug import slugify
from django.conf import settings as django_settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.forms import EmailField, ValidationError
from datetime import datetime
from django.db.utils import IntegrityError
from django.utils import translation
import sys

from launchpadlib.launchpad import Launchpad
from launchpadlib.uris import LPNET_SERVICE_ROOT


def no_credential():
    print "Can't proceed without Launchpad credential."
    sys.exit()

"""Logs into Launchpad """
#cachedir = '/Users/evgenyfadeev/.launchadlib/cache'
launchpad = Launchpad.login_with(
        'Extract Answers',
        version='devel',
        credential_save_failed=no_credential
)

user_mapping = {}


def get_questions(project_name):
    """Retrieves all questions in project_name on Launchpad"""
    project = launchpad.projects[project_name]
    return project.searchQuestions()

def get_user_data(user_link):
    """returns dictionary with keys:
    * username
    * confirmed_email_addresses
    """
    # check out user cache first
    if user_link in user_mapping:
        return user_mapping[user_link]

    username = user_link.split('~')[1]

    user_data = {
        'username': username,
        'confirmed_email_addresses': list()
    }

    lp_user = launchpad.people[username]

    for email in lp_user.confirmed_email_addresses:
        # search for the user based on their email
        email = str(email).split('/')[-1]
        user_data['confirmed_email_addresses'].append(email)

    user_mapping[user_link] = user_data
    return user_data

def get_or_create_user(user_data):
    """returns Askbot user.
    If user corresponding to the given data does not exist,
    it is created
    """
    username = user_data['username']

    #check the cache by user name
    if username in user_mapping:
        return user_mapping[username]

    try:
        # find using identical username first
        user = models.User.objects.get(username=username)
    except models.User.DoesNotExist:
        # we haven't created the user yet
        try:
            user = models.User.objects.filter(email__in=user_data['confirmed_email_addresses'])[0]
        except:
            user = models.User(username=username)
            if len(user_data['confirmed_email_addresses']):
                user.email = user_data['confirmed_email_addresses'][0]
            user.save()

    # cache the users we've seen so far to avoid API calls
    user_mapping[username] = user
    return user


def save_questions(questions, project_name, data_filename):
    """gets data from the launchpad answers and then
    saves it in the python pickled format
    so that the data can be uploaded elsewhere
    """

    #create data file if not exists
    data_file = open(data_filename, 'a+')
    data_file.close()

    #read the data file
    try:
        data_file = open(data_filename, 'r')
        question_data = pickle.load(data_file)
        data_file.close()
    except EOFError:
        question_data = dict()

    try:
        for question in questions:
            print '"' + question.title + '",' + str(question.date_created)

            if question.self_link in question_data:
                continue

            try:
                responses = question.messages_collection.entries
                print str(len(responses))
            except AttributeError:
                print "No Answers for question" + str(question)
                responses = None

            question_datum = {
                'owner': get_user_data(question.owner_link),
                'self_link': question.self_link,
                'title': question.title,
                'body_text': question.description,
                'timestamp': question.date_created.replace(tzinfo=None),
                'tags': project_name + ' migrated'
            }
            question_data[question.self_link] = question_datum
            
            answer_data = list()
            for response in responses:
                try:
                    timestamp=datetime.strptime(response['date_created'][0:-6],
                                                '%Y-%m-%dT%H:%M:%S.%f')
                except ValueError:
                    #some timestamps don't have the millisectons, thanks LP!
                    timestamp=datetime.strptime(response['date_created'][0:-6],
                                                '%Y-%m-%dT%H:%M:%S')
                if 'content' in response and len(response['content']) > 1:
                    #for some reason, Launchpad allows blank answers
                    answer = {
                        'owner': get_user_data(response['owner_link']),
                        'body_text': response['content'],
                        'timestamp': timestamp
                    }
                    answer_data.append(answer)
            question_datum['responses'] = answer_data
    finally:
        data_file = open(data_filename, 'w')
        pickle.dump(question_data, data_file)
        data_file.close()


def import_questions(data_filename):
    """loops through all items in launchpad Question format, and
    adds them as askbot Questions and Answers"""

    status_file = open('write.status', 'a')
    try:
        import_log = pickle.load(status_file)
        if not isinstance(import_log, dict):
            import_log = {}
    except:
        import_log = {}

    data_file = open(data_filename, 'r')
    questions = pickle.load(data_file)

    for question in questions.values():
        print '"' + question['title'] + '",' + str(question['timestamp'])

        try:
            responses = question['responses']
            print str(len(responses))
        except AttributeError:
            responses = None
            print "No Answers"

        if question['self_link'] in import_log:
            print "Already imported - skipping the above question"
            continue

        # post the question
        question_user = get_or_create_user(question['owner'])
        try:
            ab_question = question_user.post_question(
                title=question['title'],
                body_text=question['body_text'],
                timestamp=question['timestamp'],
                tags=question['tags']
            )
        except IntegrityError:
            # the question already exists, but we didn't find it somehow
            print "Had an IntegrityError"
            continue

        for response in question['responses']:
            if len(response['body_text']) == 0:
                continue
            response_user = get_or_create_user(response['owner'])
            #for some reason, Launchpad allows blank answers

            answer = response_user.post_answer(
                question=ab_question,
                body_text=response['body_text'],
                timestamp=response['timestamp']
            )

        import_log[question['self_link']] = 1 #mark as imported
        status_file.close()
        status_file = open('write.status', 'w')
        pickle.dump(import_log, status_file)


def main_read():
    questions = get_questions('nova')
    save_questions(questions, 'nova', 'launchpad.dat')
    print str(len(questions)) + " found"

def main_write():
    translation.activate('en')
    setting_backup = askbot_settings.LIMIT_ONE_ANSWER_PER_USER
    askbot_settings.update('LIMIT_ONE_ANSWER_PER_USER', False)
    import_questions('launchpad.dat')
    askbot_settings.update('LIMIT_ONE_ANSWER_PER_USER', setting_backup)

