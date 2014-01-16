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
cachedir = "/home/fifieldt/.launchpadlib/cache/"
launchpad = Launchpad.login_with('Extract Answers', version='devel',
                                 credential_save_failed=no_credential)

user_mapping = {}


def get_questions(project_name):
    """Retrieves all questions in project_name on Launchpad"""
    project = launchpad.projects[project_name]
    return project.searchQuestions()


def find_or_create_user(user_link):
    """Takes a Launchpad user link string of the format akin to
    https://api.staging.launchpad.net/devel/~mat-rush
    separates out the username, then uses the Launchpad user object
    to determine whether a user exists in AskBot based on the email
    addressesand username. If not, it creates one using the information
    """
    # check out user cache first
    if user_link in user_mapping:
        return user_mapping[user_link]

    username = user_link.split('~')[1]
    lp_user = launchpad.people[username]
    ab_user = None

    try:
        # find using identical username first
        ab_user = models.User.objects.get(username=username)
    except models.User.DoesNotExist:
        # we haven't created the user yet
        for email in lp_user.confirmed_email_addresses:
            # search for the user based on their email
            stripped_email = str(email).split('/')[-1]
            try:
                ab_user = models.User.objects.get(email=stripped_email)
            except models.User.DoesNotExist:
                pass

        if ab_user is None:
            # we didn't find a user, create a new one
            try:
                first_email = str(lp_user.confirmed_email_addresses[0]).split('/')[-1]
                ab_user = models.User(username=username, email=first_email)
                ab_user.save()
            except IndexError:
                try:
                    ab_user = models.User(username=username)
                    ab_user.save()
                except IntegrityError:
                    # the user already exists, but we didn't find it somehow
                    print "user is corrupt: " + user_link + str(e)
                    pass

    # cache the users we've seen so far to avoid API calls
    user_mapping[user_link] = ab_user
    if ab_user is None:
        print "ab_user still none " + user_link
    return ab_user


def import_questions(questions, project_name):
    """loops through all items in launchpad Question format, and
    adds them as askbot Questions and Answers"""

    status_file = open('write.status', 'r')
    try:
        import_log = pickle.load(status_file)
        if not isinstance(import_log, dict):
            import_log = {}
    except:
        import_log = {}

    for question in questions:
        print '"' + question.title + '",' + str(question.date_created)

        try:
            responses = question.messages_collection.entries
            print str(len(responses))
        except AttributeError:
            print "No Answers for question" + str(question)
            responses = None

        if question.self_link in import_log:
            print "Already imported - skipping the above question"
            continue

        question_user = find_or_create_user(question.owner_link)

        # post the question
        try:
            ab_question = question_user.post_question(
                title=question.title,
                body_text=question.description,
                timestamp=question.date_created.replace(tzinfo=None),
                tags=project_name + " migrated",
            )
        except IntegrityError:
            # the question already exists, but we didn't find it somehow
            print "Had an IntegrityError"
            continue
        if responses is not None:
            # post all the answers
            for response in responses:
                response_user = find_or_create_user(response['owner_link'])
                try:
                    timestamp=datetime.strptime(response['date_created'][0:-6],
                                                '%Y-%m-%dT%H:%M:%S.%f')
                except ValueError:
                    #some timestamps don't have the millisectons, thanks LP!
                    timestamp=datetime.strptime(response['date_created'][0:-6],
                                                '%Y-%m-%dT%H:%M:%S')
                if len(response['content']) > 1:
                    #for some reason, Launchpad allows blank answers
                    answer = response_user.post_answer(
                        question=ab_question,
                        body_text=response['content'],
                        timestamp=timestamp
                    )
        import_log[question.self_link] = 1 #mark as imported
        status_file.close()
        status_file = open('write.status', 'w')
        pickle.dump(import_log, status_file)


def main():
    translation.activate('en')
    questions = get_questions('nova')
    setting_backup = askbot_settings.LIMIT_ONE_ANSWER_PER_USER
    askbot_settings.update('LIMIT_ONE_ANSWER_PER_USER', False)
    print str(len(questions)) + " found"
    import_questions(questions, 'nova')
    askbot_settings.update('LIMIT_ONE_ANSWER_PER_USER', setting_backup)

if  __name__ == "__main__":
    main()
