"""LLM-assisted incident analysis. Inactive until GROQ_API_KEY is set."""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an experienced site-reliability engineer helping an on-call team. "
    "Given an incident record, its timeline and the team's comments, reply in Markdown with: "
    "1) a two-sentence summary, 2) likely causes ranked by probability, "
    "3) concrete next diagnostic steps, 4) suggested mitigation. "
    "Be specific to the evidence; say clearly when evidence is missing."
)


class AIUnavailable(Exception):
    pass


class AIError(Exception):
    pass


def is_configured():
    return bool(settings.GROQ_API_KEY)


def build_context(incident):
    lines = [
        f"Incident #{incident.pk}: {incident.title}",
        f"Service: {incident.service.name}",
        f"Severity: {incident.severity} | Status: {incident.status}",
        f"Created: {incident.created_at.isoformat()} | SLA deadline: {incident.sla_deadline.isoformat()}"
        f" | SLA breached: {incident.sla_breached}",
        f"Assigned engineers: {', '.join(a.engineer.username for a in incident.assignments.all()) or 'none'}",
        "",
        "Description:",
        incident.description or "(none)",
        "",
        "Timeline:",
    ]
    for h in incident.history.select_related("user"):
        who = h.user.username if h.user else "system"
        change = f"{h.old_value} -> {h.new_value}".strip(" ->")
        lines.append(f"- {h.created_at.isoformat()} {h.action} by {who} {change}")
    lines += ["", "Comments:"]
    comments = list(incident.comments.select_related("user"))[-50:]
    for c in comments:
        lines.append(f"- {c.user.username}: {c.content}")
    if not comments:
        lines.append("(none)")
    return "\n".join(lines)


def analyse(incident):
    if not is_configured():
        raise AIUnavailable("AI analysis is not configured. Set GROQ_API_KEY to enable it.")
    import groq

    client = groq.Groq(api_key=settings.GROQ_API_KEY, timeout=60)
    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            max_tokens=1500,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_context(incident)},
            ],
        )
    except groq.APIError as exc:
        logger.warning("AI analysis failed for incident %s: %s", incident.pk, exc)
        raise AIError("The AI provider returned an error. Try again shortly.") from exc
    text = response.choices[0].message.content or ""
    return {"analysis": text, "model": response.model}
