"""Update Event Application."""

import json
import logging
import os
from datetime import datetime

import boto3
from thirtyone import aws

root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(
    format="%(message)s", level=os.environ.get("LOG_LEVEL", logging.WARNING)
)  # pylint: disable=C0209


# Variable Reuse
dynamodb = boto3.client("dynamodb", region_name=os.environ["REGION"])
dynamodb_table = os.environ["DYNAMODB_TABLE"]
sns = boto3.client("sns")
sqs = boto3.client("sqs")


def lambda_handler(event: dict, _) -> dict:
    """Update Event details."""
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
    request = aws.get_sqs_message_with_sns_notification_from(record)
    original_event = get_event_uid_from(request["original_uid"])

    event = get_event_from(original_event["uid"])

    if event_updated(original_event=event, new_event=request):
        updated_event, _ = update_event(
            original_event=event, new_event=request
        )

        send_notification_of_updated(stage_updated(updated_event))

        delete_successfully_processed_sqs(record)


def get_event_uid_from(uid):
    """Get event user id from user id."""
    return aws.get_dynamodb_record_for(
        "original_event#{}".format(uid),
        secondary_key="original_event#{}".format(uid),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def get_event_from(uid):
    """Get event from user id."""
    return aws.get_dynamodb_record_for(
        "event#{}".format(uid),
        secondary_key="event#{}".format(uid),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def event_updated(original_event, new_event):
    """Check if event is updated."""
    updated = False
    for val in update_fields():
        if original_event.get(val) != new_event.get(val):
            updated = True
            break

    return updated


def update_fields():
    """Return updated fields."""
    return (
        "summary",
        "summary_html",
        "location",
        "location_html",
        "dtend",
        "dtstart",
        "description",
        "description_html",
    )


def update_event(original_event, new_event):
    """Update event in dynamodb."""
    for val in update_fields():
        original_event[val] = new_event.get(val)

    timestamp = int(datetime.utcnow().timestamp())
    original_event["dtstamp"] = timestamp
    original_event["last_modified"] = timestamp
    original_event["sequence"] += 1

    status = dynamodb.update_item(
        TableName=os.environ["DYNAMODB_TABLE"],
        Key={
            "pk": {"S": "event#{}".format(original_event["uid"])},
            "sk": {"S": "event#{}".format(original_event["uid"])},
        },
        UpdateExpression=(
            "SET #dtstamp = :timestamp,"
            + "#last_modified = :timestamp,"
            + "#summary = :summary,"
            + "#summary_html = :summary_html,"
            + "#location = :location,"
            + "#location_html = :location_html,"
            + "#dtend = :dtend,"
            + "#dtstart = :dtstart,"
            + "#description = :description,"
            + "#description_html = :description_html,"
            + "#sequence = :sequence"
        ),
        ExpressionAttributeNames={
            "#dtstamp": "dtstamp",
            "#last_modified": "last_modified",
            "#summary": "summary",
            "#summary_html": "summary_html",
            "#location": "location",
            "#location_html": "location_html",
            "#dtend": "dtend",
            "#dtstart": "dtstart",
            "#description": "description",
            "#description_html": "description_html",
            "#sequence": "sequence",
        },
        ExpressionAttributeValues={
            ":timestamp": {"N": str(timestamp)},
            ":summary": {"S": str(original_event["summary"])},
            ":summary_html": {"S": str(original_event["summary_html"])},
            ":location": {"S": str(original_event["location"])},
            ":location_html": {"S": str(original_event["location_html"])},
            ":dtend": {"N": str(original_event["dtend"])},
            ":dtstart": {"N": str(original_event["dtstart"])},
            ":description": {"S": str(original_event["description"])},
            ":description_html": {
                "S": str(original_event["description_html"])
            },
            ":sequence": {"N": str(original_event["sequence"])},
        },
    )

    return original_event, status


def stage_updated(event):
    """Stage updated event."""
    staged_event = {}

    stage_update_fields = [
        "description",
        "description_html",
        "dtend",
        "dtstamp",
        "dtstart",
        "last_modified",
        "location",
        "location_html",
        "status",
        "mailto",
        "method",
        "original_organizer",
        "organizer",
        "sequence",
        "summary",
        "summary_html",
        "uid",
    ]

    for field in stage_update_fields:
        staged_event[field] = event.get(field, "")

    return staged_event


def send_notification_of_updated(event):
    """Send notification of updated event."""
    return aws.publish_sns_message(
        message=json.dumps({"default": json.dumps(event)}),
        arn=os.environ["EVENT_UPDATED"],
        sns=sns,
    )


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs message."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message from request."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(identification):
    """Clean up successful tasks."""
    return aws.delete_sqs_message(
        identification, url=os.environ["SQS_URL"], sqs=sqs
    )


def cleanup_all(tasks):
    """Clean up all tasks."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
