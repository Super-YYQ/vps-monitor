import smtplib
import ssl
from email.message import EmailMessage

from app.models import MailSettings


def send_mail(config: MailSettings, subject: str, body: str):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(body)
    context = ssl.create_default_context()
    if config.security == "ssl":
        connection = smtplib.SMTP_SSL(config.host, config.port, timeout=15, context=context)
    else:
        connection = smtplib.SMTP(config.host, config.port, timeout=15)
    with connection as smtp:
        smtp.ehlo()
        if config.security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if config.username:
            smtp.login(config.username, config.password)
        refused = smtp.send_message(message)
        if refused:
            # Accepted recipients may already have received this message; surface partial delivery.
            raise smtplib.SMTPRecipientsRefused(refused)


def mail_error(error):
    # Never persist server replies: they may contain recipient addresses or credentials.
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "SMTP 认证失败，请核对账号和授权码"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "SMTP 拒绝了部分或全部收件人，请核对地址（重试可能重复投递）"
    if isinstance(error, (TimeoutError, OSError)):
        return "SMTP 连接超时或网络不可达"
    if isinstance(error, smtplib.SMTPException):
        return "SMTP 拒绝发送，请核对 TLS、发件人和服务商限制"
    return "邮件发送失败，请检查 SMTP 配置"
