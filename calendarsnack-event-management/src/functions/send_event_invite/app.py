"""Send event invite."""

import logging
import os
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
from thirtyone import aws
from thirtyone.ical import Ical

# Variable Reuse
dynamodb = boto3.client("dynamodb")
dynamodb_table = os.environ["DYNAMODB_TABLE"]
ses = boto3.client("ses", region_name=os.environ["REGION"])
sqs = boto3.client("sqs")


# Logging Configuration
root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(
    format="%(message)s", level=os.environ.get("LOG_LEVEL", logging.WARNING)
)  # pylint: disable=C0209


def lambda_handler(event, _):
    """Process API request."""
    logging.info(event)
    tasks = {}

    for record in event["Records"]:
        try:
            process_request_from(record)
        except Exception as error:  # pylint: disable=broad-except
            logging.exception(error)
            tasks["failed"] = tasks.get("failed", 0) + 1

    return cleanup_all(tasks)


def process_request_from(record):
    """Process request from record."""
    invite = aws.get_sqs_message_with_sns_notification_from(record)

    if attendee_has_not_been_sent(invite["request"]):
        create_record_of_attendee(invite)
        send_invite_to_attendee(invite)
    elif invite["request"]["origin"] == "test":
        send_invite_to_attendee(invite)

    delete_successfully_processed_sqs(record)


def attendee_has_not_been_sent(event_invite):
    """Validate attendee invite has not been sent."""
    return not aws.get_dynamodb_record_for(
        "event#{}".format(event_invite["uid"]),
        secondary_key="attendee#{}".format(event_invite["email"]),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def send_invite_to_attendee(invite):
    """Send invite to attendee."""
    return aws.send_ses_invite_to_attendee(
        ical=get_ical_for(invite),
        subject=invite["event"]["summary"],
        sender=os.environ["SENDER"].format(
            invite["event"].get("organizer", "CalendarSnack Invite")
        ),
        recipient=invite["request"]["email"],
        html=invite["event"].get("description_html", ""),
        text=invite["event"].get("description", ""),
        ses=ses,
    )


def get_ical_for(invite):
    """Compile formatted ical for invite."""
    ical = get_ical_fields_from(invite["event"])
    ical["rsvp_email"] = os.environ["RSVP_EMAIL"]
    ical["recipient"] = invite["request"]["email"]

    return Ical().build_ical_from(**ical)


def get_ical_fields_from(event):
    """Get ical fields from event."""
    ical = {}

    ical_fields = (
        "description",
        "dtend",
        "dtstart",
        "location",
        "organizer",
        "mailto",
        "sequence",
        "status",
        "summary",
        "uid",
    )

    for field in ical_fields:
        ical.update(validate_ical(field, event.get(field)))

    return ical


def validate_ical(field, value):
    """Validate ical field."""
    time_formats = ("dtend", "dtstart")

    if value:
        if field in time_formats:
            value = datetime.fromtimestamp(value).strftime("%Y%m%dT%H%M%SZ")

    return {field: value} if value else {}


def create_record_of_attendee(invite):
    """Create record of attendee from invite."""
    try:
        response = dynamodb.transact_write_items(
            TransactItems=[
                {
                    # Creates attendee invite record
                    "Put": get_attendee_record_for(
                        invite["request"], invite["event"]["mailto"]
                    )
                },
                {
                    # Decreases send limit for event
                    "Update": get_event_invite_limit_record_update_for(
                        invite["request"]
                    )
                },
                {
                    # Updates event statistics with new invite
                    "Update": get_statistics_record_update_for(
                        primary_key="event#{}".format(
                            invite["request"]["uid"]
                        ),
                        sort_key="event_statistics#{}".format(
                            invite["request"]["uid"]
                        ),
                        invite=invite["request"],
                    )
                },
                {
                    # Updates organizer statistics with new invite
                    "Update": get_statistics_record_update_for(
                        primary_key="organizer#{}".format(
                            invite["event"]["mailto"]
                        ),
                        sort_key="organizer_statistics#{}".format(
                            invite["event"]["mailto"]
                        ),
                        invite=invite["request"],
                    )
                },
                {
                    # Updates system statistics with new invite
                    "Update": get_statistics_record_update_for(
                        primary_key="system#",
                        sort_key="system_statistics#",
                        invite=invite["request"],
                    )
                },
            ]
        )
    except ClientError as error:
        raise Exception(error) from error

    return response


def get_attendee_record_for(invite, organizer):
    """Stage attendee invite record for DynamoDB."""
    timestamp = str(int(datetime.utcnow().timestamp()))

    return {
        "TableName": os.environ["DYNAMODB_TABLE"],
        "Item": {
            "pk": {"S": "event#{}".format(invite["uid"])},
            "sk": {"S": "attendee#{}".format(invite["email"])},
            "attendee": {"S": invite["email"]},
            "mailto": {"S": organizer},
            "created": {"N": timestamp},
            "name": {"S": invite.get("name", "customer")},
            "status": {"S": invite["partstat"]},
            "origin": {"S": invite["origin"]},
            "prodid": {"S": "-//31events//calendarsnack//en"},
            "history": {
                "L": [
                    {
                        "L": [
                            {"S": invite["partstat"]},
                            {"N": timestamp},
                            {"S": "-//31events//calendarsnack//en"},
                        ]
                    }
                ]
            },
        },
        "ConditionExpression": (
            "attribute_not_exists(pk) and " + "attribute_not_exists(sk)"
        ),
    }


def get_statistics_record_update_for(
    primary_key=None, sort_key=None, invite=None
):
    """Update statistics record."""
    return {
        "TableName": os.environ["DYNAMODB_TABLE"],
        "Key": {"pk": {"S": primary_key}, "sk": {"S": sort_key}},
        "UpdateExpression": (
            "SET #attendees = #attendees + :inc,"
            + "#origin.#originValue = "
            + "if_not_exists("
            + "#origin.#originValue, :zero) + :inc,"
            + "#prodid.#prodidValue = "
            + "if_not_exists("
            + "#prodid.#prodidValue, :zero) + :inc,"
            + "#rsvp.#rsvpAction ="
            + "#rsvp.#rsvpAction + :inc"
        ),
        "ExpressionAttributeNames": {
            "#attendees": "attendees",
            "#origin": "origin",
            "#originValue": invite["origin"],
            "#prodid": "prodid",
            "#prodidValue": invite["prodid"],
            "#rsvp": "rsvp",
            "#rsvpAction": invite["partstat"],
        },
        "ExpressionAttributeValues": {":inc": {"N": "1"}, ":zero": {"N": "0"}},
    }


def get_event_invite_limit_record_update_for(invite):
    """Get event invite limit record update for invite."""
    return {
        "TableName": os.environ["DYNAMODB_TABLE"],
        "Key": {
            "pk": {"S": "event#{}".format(invite["uid"])},
            "sk": {"S": "event#{}".format(invite["uid"])},
        },
        "UpdateExpression": "SET #invite_limit = #invite_limit - :decrease",
        "ExpressionAttributeNames": {"#invite_limit": "invite_limit"},
        "ExpressionAttributeValues": {":decrease": {"N": "1"}},
    }


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs messages."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(task_id):
    """Clean up successfully completed tasks."""
    return aws.delete_sqs_message(task_id, url=os.environ["SQS_URL"], sqs=sqs)


def cleanup_all(tasks):
    """Validate all tasks were cleaned up."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
