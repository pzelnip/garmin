# Garmin

So at Zapier we have a `#fun-daily-steps-challenge` Slack channel.  In that
channel folks post their daily step totals, with the goal of motivating peeps to
meet their daily goals.

When that channel started I thought "Hey, why don't we have a Zap to post my
step count into the Slack channel?", and upon looking discovered that Garmin was
one of the few apps that aren't in the Zapier marketplace, as Garmin does not
offer an open API (there's a dev API, but you
[have to apply to gain access to it](https://www.garmin.com/en-US/forms/GarminConnectDeveloperAccess/)
and it sounds like unless you're a big partner like Strava or Nike, you won't
get approved).  Boo!

BUT, looking on [Pypi](https://www.pypi.org), I found
[garminconnect](https://pypi.org/project/garminconnect/) which provides a Python
API over some of the public endpoints that garmin.com uses.  Perfect.

So I built out a Zap which uses a
[Zapier webhook](https://zapier.com/features/webhooks)
and upon getting a new entry would post a message to Slack.  I then wrote a
small Python script to pull my step data from Garmin using `garminconnect` and
then POST to that webhook to trigger the Slack message. It worked, and on
October 31st, 2022, StepBot was born:

![First Post to Slack](https://cdn.zappy.app/85d3063208af82d59ce05b8064ead3aa.png)

Since that time, this script has grown in scope.  Now it's a way for me to keep
a copy of my daily step counts in a DB, and post some more interesting metrics
like how long my current streak is.

It's also been a playground for working with new tech I hadn't used before (ex:
[SqlModel](https://sqlmodel.tiangolo.com/)).
