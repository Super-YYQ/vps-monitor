from app.mailer import send_mail
from app.models import MailSettings


def test_smtp_upgrades_tls_before_auth_and_sends_to_all_recipients(monkeypatch):
    calls = []

    class SMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, context):
            calls.append("tls")

        def login(self, username, password):
            calls.append("login")

        def send_message(self, message):
            calls.append(message)
            return {}

    monkeypatch.setattr("app.mailer.smtplib.SMTP", SMTP)
    config = MailSettings(
        enabled=True,
        host="smtp.example.com",
        username="user",
        password="secret",
        sender="from@example.com",
        recipients=["one@example.com", "two@example.com"],
    )
    send_mail(config, "Restock", "Body")
    assert calls.index("tls") < calls.index("login")
    assert str(calls[-1]["To"]) == "one@example.com, two@example.com"
    assert calls[-1].get_content().strip() == "Body"
