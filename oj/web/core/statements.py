"""
Choosing and serving statements.

Language choice, in order: an explicit ?lang= in the URL, then the reader's
saved preference, then the problem's default, then whatever exists. The choice
is remembered in the session so a student switching to Greek once does not
have to do it on every problem.
"""

from __future__ import annotations

from django.http import FileResponse, Http404

SESSION_KEY = "statement_language"


def pick(problem, request):
    """Return (chosen_statement, all_statements) or (None, [])."""
    statements = list(problem.statements.all())
    if not statements:
        return None, []

    by_language = {s.language: s for s in statements}
    requested = request.GET.get("lang")

    if requested in by_language:
        request.session[SESSION_KEY] = requested
        return by_language[requested], statements

    preferred = request.session.get(SESSION_KEY)
    if preferred in by_language:
        return by_language[preferred], statements

    for statement in statements:          # ordering puts is_default first
        if statement.is_default:
            return statement, statements
    return statements[0], statements


def serve_pdf(problem, language, request):
    """
    Serve a statement PDF through Django rather than from /static/.

    This exists so access control applies. A contest problem's statement must
    not be fetchable before the contest opens by anyone who guesses the path,
    and a static file has no way to know that.
    """
    statement = problem.statements.filter(language=language).first()
    if statement is None or not statement.pdf:
        raise Http404
    response = FileResponse(statement.pdf.open("rb"), content_type="application/pdf")
    filename = f"{problem.code}-{language}.pdf"
    # inline: the browser shows it rather than downloading, which is what a
    # competitor wants mid-contest.
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response
