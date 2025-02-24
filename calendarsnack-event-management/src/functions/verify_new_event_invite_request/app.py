"""Verify New Event Invite Application."""

import json
import logging
import os

import boto3
from thirtyone import aws

# Logging Configuration
root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(
    format="%(message)s", level=os.environ.get("LOG_LEVEL", logging.WARNING)
)  # pylint: disable=C0209


# Variable Reuse
dynamodb = boto3.client("dynamodb")
dynamodb_table = os.environ["DYNAMODB_TABLE"]
sns = boto3.client("sns")
sqs = boto3.client("sqs")


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
    request = aws.get_sqs_message_with_sns_notification_from(record)
    event = get_event_information_for(request)

    if event and verified_to_send(request, event):
        logging.info("Event send authorized")
        send_notification_of_verified(request, event)
    else:
        logging.info(f"Event send unauthorized:\n{request=}\n{event=}")

    delete_successfully_processed_sqs(record)


def verified_to_send(request, event):
    """Validate that event invite can be sent to attendee."""
    return not any(
        [
            attendee_has_blocked(event["mailto"], attendee=request["email"]),
            account_is_suspended_for(event["mailto"]),
            bulk_validation_failed(
                origin=request["origin"],
                organizer=event["mailto"],
                event=event,
            ),
        ]
    )


def get_event_information_for(request):
    """Get event information."""
    return aws.get_dynamodb_record_for(
        "event#{}".format(request["uid"]),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def attendee_has_blocked(organizer, attendee=""):
    """Validate if attendee has blocked organizer."""
    return aws.get_dynamodb_record_for(
        "attendee#{}".format(attendee),
        secondary_key="block#{}".format(organizer),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def account_is_suspended_for(organizer):
    """Validate if organizer's account is suspended."""
    return aws.get_dynamodb_record_for(
        "organizer#{}".format(organizer),
        secondary_key="suspended#{}".format(organizer),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def bulk_validation_failed(origin: str, organizer: str, event: str):
    """Check if bulk sending is requested and authorized."""
    bulk_response = False
    if origin in ["vip", "bulk"]:
        if any(
            [
                not sender_authorized_to_send_bulk_invites(organizer),
                invite_limit_reached_for(event),
            ]
        ):
            bulk_response = True

    return bulk_response


def sender_authorized_to_send_bulk_invites(organizer: str):
    """Validate if sender is authorized to send bulk invites."""
    return aws.get_dynamodb_record_for(
        f"organizer#{organizer}",
        secondary_key=f"bulk#{organizer}",
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def invite_limit_reached_for(event):
    """Validate invite limit not reached."""
    event_invite_suspended = event["invite_limit"] < 1

    # if event_invite_suspended and organizer_not_notified_of_throttled(event):
    #     send_notification_of_throttled(event)

    return event_invite_suspended


# def organizer_not_notified_of_throttled(event):
#     """Validate if organizer was notified of throttling."""
#     return not event["invite_limit_notification"]


# def send_notification_of_throttled(event):
#     """Send notification of throttling."""
#     return aws.publish_sns_message(
#         message=json.dumps({"default": json.dumps(event)}),
#         arn=os.environ["EVENT_THROTTLED"],
#         sns=sns,
#     )


def send_notification_of_verified(request, event):
    """Send notification of verified event."""
    return aws.publish_sns_message(
        message=json.dumps(
            {"default": json.dumps({"request": request, "event": event})}
        ),
        arn=os.environ["NEW_EVENT_INVITE"],
        sns=sns,
    )


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
        raise Exception(
            "{} tasks failed".format(tasks["failed"])
        )  # noqa WO719

    return "Complete"
