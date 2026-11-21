# gunicorn reads this file from its working directory at start-up and again on a
# graceful reload (kill -HUP <master pid>). Flags in deploy/nitsdate.service win over it.

# gunicorn's default access line plus how long the server spent on the request,
# e.g.  ... "GET /messages/ HTTP/1.0" 200 8123 "-" "Mozilla/5.0 ..." 24ms
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(M)sms'
