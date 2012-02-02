[DEFAULT]
bind_port = 8080
user = %USER%
swift_dir = %SWIFT_CONFIG_LOCATION%
workers = 1
log_name = swift
log_facility = LOG_LOCAL1
log_level = DEBUG

[pipeline:main]
pipeline = healthcheck cache %AUTH_SERVER% proxy-server

[app:proxy-server]
use = egg:swift#proxy
allow_account_management = true
account_autocreate = true

[filter:keystone]
use = egg:swiftkeystone2#keystone2
keystone_admin_token = %SERVICE_TOKEN%
keystone_url = http://localhost:35357/v2.0
keystone_swift_operator_roles = Member,Admin

[filter:tempauth]
use = egg:swift#tempauth
user_admin_admin = admin .admin .reseller_admin
user_test_tester = testing .admin
user_test2_tester2 = testing2 .admin
user_test_tester3 = testing3
bind_ip = 0.0.0.0

[filter:healthcheck]
use = egg:swift#healthcheck

[filter:cache]
use = egg:swift#memcache
