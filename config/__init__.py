import pymysql

# PyMySQL is a pure-Python MySQL driver, so no system libmysqlclient is needed
# (handy locally and on serverless hosts like Vercel).
pymysql.install_as_MySQLdb()
