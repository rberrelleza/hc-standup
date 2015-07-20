A simple HipChat add-on that supports async standups.

To get started, type `/standup`.

### [Install me](https://hipchat.com/addons/install?url=https%3A%2F%2Fhc-standup.herokuapp.com) ###

# Development #

## Dependencies ##
* A running instance of [mongodb](https://www.mongodb.org/)
* A running instance of [redis](https://www.redis.io/)
* An [ngrok](https://ngrok.com/) tunnel (if you want to connect to hipchat.com).
* [Python 3.3](https://www.python.org/downloads/release/python-336/) and [virtualenv](https://virtualenv.pypa.io/en/latest/)

## Running standup bot
```
#!bash

git clone git@bitbucket.org:mrdon/hc-standup.git
cd hc-standup
virtualenv -p python3.3 venv
./venv/bin/pip install -r requirements.txt 
ngrok 8080 # take note of the ngrok subdomain you've connected to and background the process 
BASE_URL=https://{ngroktunnel}.ngrok.com ./venv/bin/python3.3 app.py 
```