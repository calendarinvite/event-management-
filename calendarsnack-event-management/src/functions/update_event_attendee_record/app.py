"""Update Event Attendee Record Application."""

import logging
import os
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
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
    event_reply = aws.get_sqs_message_with_sns_notification_from(record)
    update_event_attendee_record_with(event_reply)
    delete_successfully_processed_sqs(record)


def update_event_attendee_record_with(event_reply):
    """Update event attendee record."""
    attendee_record = get_current_attendee_record_for(event_reply)

    if attendee_record.get("status", False):
        if attendee_record.get("status", "") != event_reply["partstat"]:
            submit_updated_event_attend_record_for(
                event_reply, previous_status=attendee_record["status"]
            )
        else:
            logging.warning("RSVP already processed")
    else:
        logging.warning("Attendee does not exist")
        submit_shared_event_attend_record_for(event_reply)


def get_current_attendee_record_for(event_reply):
    """Get current attendee record."""
    return aws.get_dynamodb_record_for(
        "event#{}".format(event_reply["uid"]),
        secondary_key="attendee#{}".format(event_reply["attendee"]),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def submit_updated_event_attend_record_for(event_reply, previous_status=None):
    """Submit updated event attendee record."""
    try:
        organizer = get_organizer_for(event_reply["uid"])

        response = dynamodb.transact_write_items(
            TransactItems=[
                {
                    # Updates event attendee record with new reply
                    "Update": get_attendee_update_record_for(event_reply)
                },
                {
                    # Updates event statistics with new reply
                    "Update": get_statistics_record_update_for(
                        event_reply,
                        primary_key="event#{}".format(event_reply["uid"]),
                        sort_key="event_statistics#{}".format(
                            event_reply["uid"]
                        ),
                        previous_status=previous_status,
                    )
                },
                {
                    # Updates organizer statistics with new reply
                    "Update": get_statistics_record_update_for(
                        event_reply,
                        primary_key="organizer#{}".format(organizer),
                        sort_key="organizer_statistics#{}".format(organizer),
                        previous_status=previous_status,
                    )
                },
                {
                    # Updates system statistics with new reply
                    "Update": get_statistics_record_update_for(
                        event_reply,
                        primary_key="system#",
                        sort_key="system_statistics#",
                        previous_status=previous_status,
                    )
                },
            ]
        )
    except ClientError as error:
        raise Exception(error) from error

    return response


def submit_shared_event_attend_record_for(event_reply):
    """Submit shared event attendee record."""
    try:
        organizer = get_organizer_for(event_reply["uid"])

        response = dynamodb.transact_write_items(
            TransactItems=[
                {
                    # Updates event attendee record with new reply
                    "Put": get_shared_attendee_record_for(event_reply)
                },
                {
                    # Updates event statistics with new reply
                    "Update": get_statistics_record_update_for(
                        event_reply,
                        primary_key="event#{}".format(event_reply["uid"]),
                        sort_key="event_statistics#{}".format(
                            event_reply["uid"]
                        ),
                        shared=True,
                    )
                },
                {
                    # Updates organizer statistics with new reply
                    "Update": get_statistics_record_update_for(
                        event_reply,
                        primary_key="organizer#{}".format(organizer),
                        sort_key="organizer_statistics#{}".format(organizer),
                        shared=True,
                    )
                },
                {
                    # Updates system statistics with new reply
                    "Update": get_statistics_record_update_for(
                        event_reply,
                        primary_key="system#",
                        sort_key="system_statistics#",
                        shared=True,
                    )
                },
            ]
        )
    except ClientError as error:
        raise Exception(error) from error

    return response


def get_attendee_update_record_for(event_reply):
    """Get attendee updated record."""
    timestamp = str(int(datetime.utcnow().timestamp()))

    return {
        "TableName": os.environ["DYNAMODB_TABLE"],
        "Key": {
            "pk": {"S": "event#{}".format(event_reply["uid"])},
            "sk": {"S": "attendee#{}".format(event_reply["attendee"])},
        },
        "UpdateExpression": (
            "SET #created = :timestamp,"
            + "#prodid = :prodid,"
            + "#status = :status,"
            + "#history = list_append(#history, :history)"
        ),
        "ExpressionAttributeNames": {
            "#created": "created",
            "#history": "history",
            "#prodid": "prodid",
            "#status": "status",
        },
        "ExpressionAttributeValues": {
            ":timestamp": {"N": timestamp},
            ":prodid": {"S": event_reply["prodid"]},
            ":status": {"S": event_reply["partstat"]},
            ":history": {
                "L": [
                    {
                        "L": [
                            {"S": event_reply["partstat"]},
                            {"S": timestamp},
                            {"S": event_reply["prodid"]},
                        ]
                    }
                ]
            },
        },
        "ConditionExpression": (
            "attribute_exists(pk) and attribute_exists(sk)"
        ),
    }


def get_shared_attendee_record_for(event_reply):
    """Stages attendee invite record for DynamoDB."""
    timestamp = str(int(datetime.utcnow().timestamp()))

    return {
        "TableName": os.environ["DYNAMODB_TABLE"],
        "Item": {
            "pk": {"S": "event#{}".format(event_reply["uid"])},
            "sk": {"S": "attendee#{}".format(event_reply["attendee"])},
            "mailto": {"S": get_organizer_for(event_reply["uid"])},
            "attendee": {"S": event_reply["attendee"]},
            "created": {"N": timestamp},
            "name": {"S": event_reply.get("name", "customer")},
            "status": {"S": event_reply["partstat"]},
            "origin": {"S": "shared"},
            "prodid": {"S": event_reply["prodid"]},
            "history": {
                "L": [
                    {
                        "L": [
                            {"S": event_reply["partstat"]},
                            {"N": timestamp},
                            {"S": event_reply["prodid"]},
                        ]
                    }
                ]
            },
        },
        "ConditionExpression": (
            "attribute_not_exists(pk) and attribute_not_exists(sk)"
        ),
    }


def get_organizer_for(uid):
    """Get organizer info for uid."""
    return aws.get_dynamodb_record_for(
        "event#{}".format(uid),
        secondary_key="event#{}".format(uid),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )["mailto"]


def get_statistics_record_update_for(
    event_reply,
    primary_key=None,
    sort_key=None,
    previous_status=None,
    shared=False,
):
    """Get statistics record update."""
    update_expression = "SET #rsvp.#rsvpAction = #rsvp.#rsvpAction + :inc"
    expression_attribute_values = {":inc": {"N": "1"}}

    expression_attribute_names = {
        "#rsvp": "rsvp",
        "#rsvpAction": event_reply["partstat"],
    }

    if not shared:
        # Configures request for an existing attendee
        expression_attribute_names["#rsvpPreviousAction"] = previous_status

        update_expression += (
            ",#rsvp.#rsvpPreviousAction = #rsvp.#rsvpPreviousAction - :inc"
        )
    else:
        # Configures request for new attendee with shared origin
        expression_attribute_names["#attendees"] = "attendees"
        expression_attribute_names["#origin"] = "origin"
        expression_attribute_names["#originValue"] = "shared"
        expression_attribute_names["#prodid"] = "prodid"
        expression_attribute_names["#prodidValue"] = event_reply["prodid"]
        expression_attribute_values[":zero"] = {"N": "0"}

        update_expression += (
            ",#attendees = #attendees + :inc,"
            + "#origin.#originValue = "
            + "if_not_exists(#origin.#originValue, :zero) + :inc,"
            + "#prodid.#prodidValue = "
            + "if_not_exists(#prodid.#prodidValue, :zero) + :inc"
        )

    return {
        "TableName": os.environ["DYNAMODB_TABLE"],
        "Key": {"pk": {"S": primary_key}, "sk": {"S": sort_key}},
        "UpdateExpression": update_expression,
        "ExpressionAttributeNames": expression_attribute_names,
        "ExpressionAttributeValues": expression_attribute_values,
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
