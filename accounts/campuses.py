"""The NITs nitsdate serves. A student's NIT ("campus") comes from their college email.

Only NITs whose student mail runs on Google Workspace are listed, since signing in is with Google.
Checked from each domain's mail (MX) records on 2026-11-21. Left out for now:
  NIT Agartala (nita.ac.in)       – government mail cloud (mgovcloud), not Google
  NIT Nagaland (nitnagaland.ac.in) – Microsoft 365
  NIT Tiruchirappalli (nitt.edu)   – self-hosted mail
  NIT Andhra Pradesh (nitandhra.ac.in) – the domain didn't resolve at all; add it once it does
  nitk.ac.in (NITK's old domain)   – self-hosted; NITK students use nitk.edu.in
To add a NIT, append it below; settings.OPEN_CAMPUSES decides which ones may sign in.
"""
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class Campus:
    code: str
    name: str
    domains: tuple

    def __str__(self):
        return self.name


CAMPUSES = (
    Campus("arunachal", "NIT Arunachal Pradesh", ("nitap.ac.in",)),
    Campus("calicut", "NIT Calicut", ("nitc.ac.in",)),
    Campus("delhi", "NIT Delhi", ("nitdelhi.ac.in",)),
    Campus("durgapur", "NIT Durgapur", ("nitdgp.ac.in",)),
    Campus("goa", "NIT Goa", ("nitgoa.ac.in",)),
    Campus("hamirpur", "NIT Hamirpur", ("nith.ac.in",)),
    Campus("jalandhar", "NIT Jalandhar", ("nitj.ac.in",)),
    Campus("jamshedpur", "NIT Jamshedpur", ("nitjsr.ac.in",)),
    Campus("surathkal", "NIT Karnataka", ("nitk.edu.in",)),
    Campus("kurukshetra", "NIT Kurukshetra", ("nitkkr.ac.in",)),
    Campus("manipur", "NIT Manipur", ("nitmanipur.ac.in",)),
    Campus("meghalaya", "NIT Meghalaya", ("nitm.ac.in",)),
    Campus("mizoram", "NIT Mizoram", ("nitmz.ac.in",)),
    Campus("patna", "NIT Patna", ("nitp.ac.in",)),
    Campus("puducherry", "NIT Puducherry", ("nitpy.ac.in",)),
    Campus("raipur", "NIT Raipur", ("nitrr.ac.in",)),
    Campus("rourkela", "NIT Rourkela", ("nitrkl.ac.in",)),
    Campus("sikkim", "NIT Sikkim", ("nitsikkim.ac.in",)),
    Campus("silchar", "NIT Silchar", ("nits.ac.in",)),
    Campus("srinagar", "NIT Srinagar", ("nitsri.ac.in", "nitsri.net")),
    Campus("uttarakhand", "NIT Uttarakhand", ("nituk.ac.in",)),
    Campus("warangal", "NIT Warangal", ("nitw.ac.in",)),
    Campus("jaipur", "MNIT Jaipur", ("mnit.ac.in",)),
    Campus("bhopal", "MANIT Bhopal", ("manit.ac.in",)),
    Campus("allahabad", "MNNIT Allahabad", ("mnnit.ac.in",)),
    Campus("surat", "SVNIT Surat", ("svnit.ac.in",)),
    Campus("nagpur", "VNIT Nagpur", ("vnit.ac.in",)),
)
CAMPUS_CHOICES = [(c.code, c.name) for c in sorted(CAMPUSES, key=lambda c: c.name)]
_BY_CODE = {c.code: c for c in CAMPUSES}


def by_code(code):
    return _BY_CODE.get(code)


def campus_name(code):
    campus = _BY_CODE.get(code)
    return campus.name if campus else ""


def campus_of_email(email):
    """The NIT an email address belongs to (also its departmental subdomains: x@cse.nits.ac.in),
    or None. A look-alike such as x@evilnits.ac.in doesn't count."""
    domain = (email or "").rpartition("@")[2].strip().lower()
    if not domain:
        return None
    for campus in CAMPUSES:
        if any(domain == d or domain.endswith("." + d) for d in campus.domains):
            return campus
    return None


def open_campuses():
    """The NITs whose students may sign in (settings.OPEN_CAMPUSES), by name."""
    codes = settings.OPEN_CAMPUSES
    if codes in ("all", "any"):
        return tuple(sorted(CAMPUSES, key=lambda c: c.name))
    return tuple(sorted((c for c in CAMPUSES if c.code in codes), key=lambda c: c.name))


def is_open(campus):
    codes = settings.OPEN_CAMPUSES
    return campus is not None and (codes in ("all", "any") or campus.code in codes)


def default_campus():
    return settings.DEFAULT_CAMPUS


def campus_for_new_account(email):
    """Campus code for an account: from its college email; an outsider gets the campus a moderator
    chose when letting them in, and anyone else (admins) the default campus."""
    campus = campus_of_email(email)
    if campus:
        return campus.code
    if email:
        from moderation.models import AllowedEmail
        chosen = AllowedEmail.objects.filter(email__iexact=email).values_list("campus", flat=True).first()
        if chosen:
            return chosen
    return settings.DEFAULT_CAMPUS
