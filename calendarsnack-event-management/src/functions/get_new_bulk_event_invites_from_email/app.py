"""Process bulk event invites from email with attendee information in CSV.

Sender must be an authorized bulk sender and an authorized organizer of the
events to send invites successfully to attendees.
"""

import email
import json
import logging
import re
from os import environ

import boto3


def lambda_handler(event, _):
    """Handle lambda event."""
    logging.info(event)
    process_bulk_event_invites(event["Records"][0])


def process_bulk_event_invites(event):
    """Process bulk event invites."""
    # Add Try/Except --> Send status report to sender
    event_request = get_sqs_message_with_s3_sns_notification(event)
    bucket, key = get_s3_file_location_from_sns_notification(event_request)
    sender, email_request = get_email_request(get_s3_file_content(bucket, key))

    if sender_authorized_to_send_bulk_invites(sender):
        logging.info(f"Authorized bulk sender: {sender}")
        event_invites, invalid_invites = get_event_invites(
            sender, email_request
        )
        for event_ in event_invites:
            event_record = get_event_record(event_)

            if event_record and sender_can_invite(sender, event_record):
                queue_event_invites_for_send(
                    event_record, event_invites[event_]
                )
            else:
                logging.info(f"Invalid Request: [{sender}] {event}")
                invalid_invites.setdefault(event_, []).extend(
                    event_invites[event_]
                )

        send_sender_status_of_bulk_request(
            sender, event_invites, invalid_invites
        )
    else:
        logging.info(f"Unauthorized bulk sender: {sender}")


def get_sqs_message_with_s3_sns_notification(lambda_event):
    """Get sqs message with s3 event notification."""
    return json.loads(json.loads(lambda_event["body"])["Message"])[
        "Records"
    ].pop()


def get_s3_file_location_from_sns_notification(sns_notification):
    """Get s3 file location from sns."""
    return (
        sns_notification["s3"]["bucket"]["name"],
        sns_notification["s3"]["object"]["key"],
    )


def get_s3_file_content(bucket, key):
    """Get s3 file content."""
    return (
        S3.get_object(Bucket=bucket, Key=key)["Body"]
        .read()
        .decode("utf-8-sig")
    )


def get_email_request(s3_file):
    """Get email request."""
    email_request = parse_email(s3_file)
    return (get_sender_email(email_request["from"]), email_request)


def parse_email(email_request):
    """Parse email."""
    return email.parser.Parser().parsestr(email_request)


def get_sender_email(email_from_header):
    """Get sender email."""
    return email.utils.parseaddr(email_from_header)[-1]


def sender_authorized_to_send_bulk_invites(sender):
    """Validate if sender is authorized to send bulk invites."""
    table_output = DYNAMODB.get_item(
        TableName=environ["THIRTYONE_TABLE"],
        Key={
            "pk": {"S": f"organizer#{sender}"},
            "sk": {"S": f"bulk#{sender}"},
        },
        ReturnConsumedCapacity="NONE",
    ).get("Item", {})

    return table_output


def get_event_invites(
    sender, email_request
):  # pylint: disable=unused-argument
    """
    Get event invites.

    Returns tuple with the first element as a dict of event UIDs
    with an array of event invites that fall under each uid.
    The second element of the tuple is a dict of event UIDs
    with an array of invalid invites as dicts.

    return (
        {'valid_invite_uid': [{'email': email, 'name': name}]},
        {'invalid_invite_uid': [{'email': email, 'name': name}]}
    )
    """
    event_invites = {}
    invalid_invites = {}
    csv_data, csv_headers = get_event_invites_csv_content(email_request)
    unvalidated_event_invites = get_event_invites_from_csv(
        csv_headers, csv_data
    )

    for invite in unvalidated_event_invites:
        attendee_email = standardize_attendee_email(invite.get("email", ""))

        if all(
            [
                valid_email(attendee_email),
                allowed_to_invite(sender, attendee_email),
            ]
        ):
            event_invites.setdefault(invite["uid"].lower(), []).append(
                {
                    "email": attendee_email,
                    "name": standardize_attendee_name(invite.get("name", "")),
                    "origin": "bulk",
                    "partstat": "noaction",
                    "prodid": "31events//ses",
                    "uid": invite["uid"].lower(),
                }
            )
        elif populated_invite_information(invite):
            invalid_invites.setdefault(invite["uid"].lower(), []).append(
                {"email": attendee_email, "name": invite.get("name", "")}
            )

    return (event_invites, invalid_invites)


def populated_invite_information(invite):
    """Populate invite information."""
    return (
        invite.get("uid", False)
        and invite.get("email", False)
        and invite.get("name", False)
    )


def get_event_invites_csv_content(email_request):
    """Get event invites from csv."""
    csv = bytes()
    for section in email_request.walk():
        if section.get_content_type() == "text/csv":
            csv = section.get_payload(decode=True)
            break

    return get_csv_information(csv.decode("utf-8-sig"))


def get_csv_information(event_invites_csv):
    """Get csv information."""
    csv_data = event_invites_csv.split("\r\n")
    if len(csv_data) == 1:
        csv_data = event_invites_csv.split("\n")
    csv_headers = csv_data.pop(0).strip().lower().split(",")
    logging.info(f"{csv_headers=}: {csv_data=}")
    return (csv_data, csv_headers)


def get_event_invites_from_csv(csv_headers, csv_data):
    """Return list of invites that have been converted to dict."""
    return [get_formatted_invite(csv_headers, invite) for invite in csv_data]


def get_formatted_invite(csv_headers, invite):
    """
    Remove whitespaces and lowercase values in the invite.

    Invite is returned as a dictionary.
    """
    return dict(zip(csv_headers, invite.strip().split(",")))


def standardize_attendee_email(attendee_email):
    """Standardize attendee email."""
    return attendee_email.strip().lower()


def valid_email(attendee_email):
    """Validate attendee email."""
    return EMAIL_REGEX.match(attendee_email)


def standardize_attendee_name(name):
    """Standardize attendee name."""
    return name if name else "customer"


def allowed_to_invite(sender, attendee_email):
    """Validate that an event invite can be sent to an attendee."""
    return bool(organizer_blocked(sender, attendee_email))


def organizer_blocked(organizer, attendee=""):
    """Validate if organizer is blocked."""
    return DYNAMODB.get_item(
        TableName=environ["THIRTYONE_TABLE"],
        Key={
            "pk": {"S": f"attendee#{attendee}"},
            "sk": {"S": f"block#{organizer}"},
        },
        ReturnConsumedCapacity="NONE",
    ).get("Item", {})


def get_event_record(event):
    """Get event record."""
    dynamodb_record = DYNAMODB.get_item(
        TableName=environ["THIRTYONE_TABLE"],
        Key={"pk": {"S": f"event#{event}"}, "sk": {"S": f"event#{event}"}},
        ReturnConsumedCapacity="NONE",
    ).get("Item", {})

    formatted_record = {}

    if dynamodb_record:
        for field in dynamodb_record.keys():
            formatted_record.update(
                get_value_from_dynamodb(field, dynamodb_record[field])
            )

    return formatted_record


def get_value_from_dynamodb(field, value):
    """Get value from dynamodb."""
    if value.get("N", False):
        value["N"] = int(value["N"])

    return {field: value.popitem()[1]}


def sender_can_invite(sender, event_record):
    """Validate that sender can invite."""
    # Future Implementation: add records for authorized senders
    return sender == event_record["mailto"]


def queue_event_invites_for_send(event, event_invites):
    """Queue event invites for send."""
    for event_invite in event_invites:
        queue_event_invite_for_send(event, event_invite)


def queue_event_invite_for_send(event, event_invite):
    """Queue event invite for send."""
    SNS.publish(
        TargetArn=environ["NEW_EVENT_INVITE"],
        MessageStructure="json",
        Message=json.dumps(
            {"default": json.dumps({"request": event_invite, "event": event})}
        ),
    )


def send_sender_status_of_bulk_request(
    sender,
    event_invites,
    invalid_invites,
    charset="UTF-8",
    subject="calendarsnack.com Bulk Invite Status",
):
    """Send standard email."""
    recipient = {"ToAddresses": [sender]}
    message = {"Subject": {"Charset": charset, "Data": subject}}

    html, text = format_bulk_event_invites_statistics(
        event_invites, invalid_invites
    )

    message["Body"] = {"Html": {"Charset": charset, "Data": html}}
    message["Body"] = {"Text": {"Charset": charset, "Data": text}}

    return SES.send_email(
        Source=environ["SYSTEM_EMAIL"], Destination=recipient, Message=message
    )


def format_bulk_event_invites_statistics(event_invites, invalid_invites):
    """Format bulk event invite statistics."""
    statistics_report = ""

    for event in event_invites:
        statistics_report += (
            f"{event}: {len(event_invites[event])} invites sent\n"
        )

    for event in invalid_invites:
        statistics_report += (
            f"{event}: {len(invalid_invites[event])} not sent.\n"
        )

    return (format_for_html(statistics_report), statistics_report)


def format_for_html(text):
    """Format html."""
    return text.replace("\n", "<br>")


def get_email_regex():
    """Get email regex."""
    return re.compile(
        "".join(
            (
                r"(^[^\s\@\:\;\"'\?]+@",
                r"([^\s\.\@\:\;\"'\?]+\.){1,}",
                r"[a-z0-9]{2,}$)",
            )
        )
    )


def configure_logging(level="WARNING"):
    """Configure logging that can be dynamically adjusted."""
    root = logging.getLogger()

    if root.handlers:
        for handler in root.handlers:
            root.removeHandler(handler)

    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(funcName)s(): %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


# Lambda variable re-use
EMAIL_REGEX = get_email_regex()
REGION = environ.get("REGION", "us-west-2")
DYNAMODB = boto3.client("dynamodb", region_name=REGION)
S3 = boto3.client("s3", region_name=REGION)
SES = boto3.client("ses", region_name=REGION)
SNS = boto3.client("sns", region_name=REGION)
configure_logging(level=environ.get("LOG_LEVEL", "WARNING"))
