"""
Email service — Resend-backed transactional emails.

Falls back gracefully when RESEND_API_KEY is not set (logs a warning, does not crash).
All public functions are fire-and-forget from the caller's perspective; errors are caught
and logged rather than propagated so that a failing email never blocks a user action.
"""

import logging
import resend
from app.core.config import settings

logger = logging.getLogger(__name__)


def _client_ready() -> bool:
    if not settings.RESEND_API_KEY:
        logger.warning("Email not sent — RESEND_API_KEY not configured")
        return False
    resend.api_key = settings.RESEND_API_KEY
    return True


def _send(*, to: str | list[str], subject: str, html: str) -> None:
    if not _client_ready():
        return
    try:
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": to if isinstance(to, list) else [to],
            "subject": subject,
            "html": html,
        })
        logger.info("Email sent: %s → %s", subject, to)
    except Exception as exc:
        logger.error("Email send failed (%s): %s", subject, exc)


# ── Templates ──────────────────────────────────────────────────────────────────

def _base(body: str) -> str:
    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                max-width:560px;margin:0 auto;padding:40px 24px;color:#111;">
      <div style="margin-bottom:32px;">
        <span style="font-size:13px;letter-spacing:0.12em;text-transform:uppercase;
                     color:#888;font-family:monospace;">Senebiclabs</span>
      </div>
      {body}
      <div style="margin-top:48px;padding-top:24px;border-top:1px solid #eee;
                  font-size:12px;color:#999;line-height:1.6;">
        Senebiclabs · Data Infrastructure for Medical AI<br>
        This email was sent because you interacted with senebiclabs.com.
      </div>
    </div>
    """


# ── Expert application ─────────────────────────────────────────────────────────

def send_expert_application_confirmation(*, name: str, email: str) -> None:
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 16px;">You are on the list.</h2>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 16px;">
        Dear Dr. {name},
      </p>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 32px;">
        We are building the Senebiclabs mobile app. Your place in the specialist network
        is reserved. When the app is ready to launch, we will reach out to complete your
        profile and get you activated on the network.
      </p>
      <p style="font-size:14px;color:#666;line-height:1.6;">
        Questions? Reply to this email and we will get back to you.
      </p>
    """)
    _send(to=email, subject="You are on the list — Senebiclabs Specialist Network", html=html)


def send_expert_application_admin_alert(
    *, name: str, email: str, note: str | None,
) -> None:
    note_row = f"<tr><td style='padding:6px 12px 6px 0;color:#666;'>Note</td><td style='padding:6px 0;'>{note}</td></tr>" if note else ""
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 32px;">New specialist sign-up</h2>
      <table style="width:100%;border-collapse:collapse;font-size:15px;">
        <tr><td style="padding:6px 12px 6px 0;color:#666;">Name</td><td style="padding:6px 0;font-weight:500;">{name}</td></tr>
        <tr><td style="padding:6px 12px 6px 0;color:#666;">Email</td><td style="padding:6px 0;">{email}</td></tr>
        {note_row}
      </table>
      <div style="margin-top:32px;">
        <a href="{settings.SUPABASE_URL.replace('.supabase.co','').replace('https://','https://supabase.com/dashboard/project/')}/editor"
           style="display:inline-block;background:#111;color:#fff;padding:10px 20px;
                  border-radius:6px;font-size:13px;text-decoration:none;">
          View in Supabase →
        </a>
      </div>
    """)
    _send(
        to=settings.ADMIN_EMAIL,
        subject=f"New specialist sign-up — {name}",
        html=html,
    )


def send_expert_approved(*, name: str, email: str) -> None:
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 16px;">You are confirmed on the list.</h2>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 16px;">
        Dear Dr. {name},
      </p>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 32px;">
        You are confirmed on the Senebiclabs pre-launch specialist list. When the mobile
        app launches, we will reach out to complete your profile and activate you on the
        network. Patients whose symptoms match your specialty and location will be routed
        to you from day one.
      </p>
      <p style="font-size:14px;color:#666;">
        Welcome to the network.
      </p>
    """)
    _send(to=email, subject="You are confirmed — Senebiclabs Specialist Network", html=html)


def send_expert_rejected(*, name: str, email: str) -> None:
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 16px;">Your Senebiclabs application</h2>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 16px;">
        Dear Dr. {name},
      </p>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 16px;">
        Thank you for applying to the Senebiclabs specialist network. After reviewing
        your application, we are not able to move forward at this time.
      </p>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 32px;">
        If you believe this is an error or would like to discuss your application,
        please reply to this email and we will get back to you.
      </p>
    """)
    _send(to=email, subject="Your Senebiclabs application", html=html)


# ── Project submission (data annotation) ───────────────────────────────────────

def send_project_submission_confirmation(*, name: str, email: str) -> None:
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 16px;">Thanks, {name}.</h2>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 32px;">
        We have received your project. Our team will review it and be in touch within one
        business day to scope a pilot.
      </p>
      <p style="font-size:14px;color:#666;line-height:1.6;">
        Questions? Reply to this email and we will get back to you.
      </p>
    """)
    _send(to=email, subject="We received your project — Senebiclabs", html=html)


def send_project_submission_admin_alert(*, submission) -> None:
    def _row(label: str, value) -> str:
        return (f"<tr><td style='padding:6px 12px 6px 0;color:#666;'>{label}</td>"
                f"<td style='padding:6px 0;'>{value or '—'}</td></tr>")
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 32px;">New project submission</h2>
      <table style="width:100%;border-collapse:collapse;font-size:15px;">
        {_row('Company', submission.company)}
        {_row('Name', submission.name)}
        {_row('Email', submission.email)}
        {_row('Data type', submission.data_type)}
        {_row('Task type', submission.task_type)}
        {_row('Volume', submission.volume)}
        {_row('Timeline', submission.timeline)}
        {_row('Sensitivity', submission.data_sensitivity)}
        {_row('Sample', submission.sample_link)}
      </table>
      <p style="font-size:14px;color:#333;line-height:1.6;margin:20px 0 0;white-space:pre-wrap;">{submission.description}</p>
      <div style="margin-top:32px;">
        <a href="{settings.SUPABASE_URL.replace('.supabase.co','').replace('https://','https://supabase.com/dashboard/project/')}/editor"
           style="display:inline-block;background:#111;color:#fff;padding:10px 20px;
                  border-radius:6px;font-size:13px;text-decoration:none;">
          View in Supabase →
        </a>
      </div>
    """)
    _send(to=settings.ADMIN_EMAIL, subject=f"New project — {submission.company}", html=html)


def send_portal_link(*, email: str, link: str) -> None:
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 16px;">Your project portal</h2>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 28px;">
        Click below to view the status of your project with Senebiclabs. This link
        works for 14 days and is tied to this email address.
      </p>
      <div style="margin:0 0 28px;">
        <a href="{link}"
           style="display:inline-block;background:#111;color:#fff;padding:13px 26px;
                  border-radius:8px;font-size:14px;text-decoration:none;">
          Open my project →
        </a>
      </div>
      <p style="font-size:13px;color:#666;line-height:1.6;">
        If you did not request this, you can safely ignore this email.
      </p>
    """)
    _send(to=email, subject="Your Senebiclabs project portal", html=html)


# ── Waitlist ───────────────────────────────────────────────────────────────────

_WAITLIST_MESSAGES = {
    "patient": (
        "You are on the list.",
        "We are building the patient portal now. When it is ready, you will be among the first to know.",
    ),
    "contributor": (
        "You are on the contributor list.",
        "We are setting up the contributor programme and will be in touch when it opens.",
    ),
    "researcher": (
        "You are on the list.",
        "We will reach out when research access becomes available.",
    ),
}


def send_waitlist_confirmation(*, email: str, type: str) -> None:
    heading, body = _WAITLIST_MESSAGES.get(
        type,
        ("You are on the list.", "We will be in touch.")
    )
    html = _base(f"""
      <h2 style="font-size:22px;font-weight:400;margin:0 0 16px;">{heading}</h2>
      <p style="font-size:16px;line-height:1.7;color:#333;margin:0 0 32px;">
        {body}
      </p>
      <p style="font-size:14px;color:#666;line-height:1.6;">
        Questions? Reply to this email.
      </p>
    """)
    _send(to=email, subject=f"{heading} — Senebiclabs", html=html)
