# Hass Status

Hass Status is a Home Assistant integration that turns sensor readings into a verdict: OK, attention, or critical. A check can test one entity or combine several. Categories group those checks the way labels do, with a name, icon, and color. A group rolls checks up into one row. One sensor counts how many checks are open.

The check entity stores the verdict, not the reading. A pH sensor that updates every 30 seconds already writes about a million states over two years of history. The check writes a new state only when the verdict changes. The live number stays on the original sensor. Cards and automations read it from there.

This is the first slice: checks, groups, and the open-issue count. Notifications and a dashboard card are not included yet.

## Install

Home Assistant 2025.4 or newer is required.

On Home Assistant OS, open the Terminal & SSH add-on and run:

```bash
mkdir -p /config/custom_components
cd /tmp
wget -O hass_status.tar.gz https://github.com/evercape/hass_status/archive/refs/heads/main.tar.gz
tar -xzf hass_status.tar.gz
rm -rf /config/custom_components/hass_status
mv hass_status-main/custom_components/hass_status /config/custom_components/hass_status
```

Restart Home Assistant. A browser refresh does not load the new files. Then go to **Settings → Devices & services → Integrations**, search for **Hass Status**, and add it.

Updating is the same commands. Restart Home Assistant after replacing the folder.

With HACS, add `https://github.com/evercape/hass_status` as a custom repository of type Integration, then download it.

## Create a board

The integration creates a board. Give it a name, such as `Home`. You can add more than one board if you want separate counts.

On the integration page, the buttons are **Add check**, **Add group**, and **Add category**.

- **Category** for a label: name, icon, and color, edited together
- **Check** for a verdict
- **Group** for a row made of checks

## A single reading

Add a check:

| Field | Example |
| --- | --- |
| Name | Pool pH |
| Id | `pool_ph` |
| Category | New category, then name Pool, an icon, and a color |
| Value to show when OK | your pH sensor |
| Logic | All conditions pass |
| Hold | empty, or a few minutes if the reading blips |

Add a condition:

| Field | Example |
| --- | --- |
| Condition id | `ph` |
| Type | Numeric range |
| Entity | the same pH sensor |
| Severity when it fails | Attention |
| Minimum | 6.5 |
| Maximum | 8.5 |
| Hysteresis | 0.1 |

Equal to 6.5 or 8.5 is OK. After it goes outside, it must return at least 0.1 past the limit before the check clears. Sentences are optional. If you write `pH is {value}, above {max}`, that text is stored with the placeholders. The check does not fill in the live number, because doing so would copy the sensor history.

## Several sensors, one verdict

A house-water check can require all of these:

1. `pressure` — numeric range 3 to 4 on the pressure sensor, severity Attention
2. `leak` — expected state `off` on the leak sensor, severity Critical
3. `pump` — expected state `off` on the fault sensor, severity Critical

Logic: **All conditions pass**. While everything passes, the row points at the pressure sensor. If the leak opens, the check points at the leak sensor and the severity is Critical, because that is the worst failure.

**Any condition passes** is the other built-in rule: the check is OK when at least one condition passes. Use it for “the internet is up if the ping or the router is up”.

**Formula** is for anything else. Write each condition as the healthy case, then combine the condition ids with `and`, `or`, `not`, and parentheses. A name is true when that condition passes:

```text
pressure and leak
```

`not` is for a condition you defined as the unwanted state. Ids are lowercase. `and`, `or`, and `not` cannot be used as ids.

**Template** conditions are the escape hatch. A rendered true value means the condition passes. False means it failed. A template error counts as not reporting.

**Expected state** is an exact match (`on`, `off`, `home`, or any other state string). **Is not** inverts it. Unknown and unavailable always count as not reporting, for both comparisons.

## Groups and the counter

Create the checks first, then add a group and pick the checks that belong to a category. The group turns on when any of those checks is on. Its message is the name of each open check, not the sensor reading.

The board has one sensor, **Open issues**. Its state is how many checks are on. Groups are not counted again. The attributes are:

- `checks` — ids of the open checks
- `by_category` — open count for each category, including zeros

The icon is a check mark at zero and an alert when the count is above zero. A dashboard can hide the card when the state is 0. That card is step 2; the sensor is ready now.

## What each check entity exposes

Every check is a binary sensor on the board device, with device class **Problem**. On means it needs attention. Off means OK. Home Assistant names it from the board and the check, so a board called Home and a check called Pool pH shows up as Home Pool pH. You can rename the entity in the UI; the id you entered stays in the `object_id` attribute.

| Attribute | Meaning |
| --- | --- |
| `severity` | `ok`, `attention`, or `critical` |
| `reason` | `ok`, `low`, `high`, `state`, `unavailable`, `template`, or `formula` |
| `focus_entity` | Entity to display. The configured value while OK, otherwise the entity that failed |
| `display_entity` | The value chosen for the OK state |
| `sources` | Entities the conditions read |
| `failed_conditions` | Condition ids that are not passing |
| `active_condition` | The condition that set the severity |
| `message` | Sentence for the current reason. Placeholders are not filled in |
| `object_id` | Stable id |
| `category` | Category, when set |
| `logic` | `all`, `any`, or `formula` |

The same attributes are what a later card will use. It can read `focus_entity` from the state machine at display time and show that sensor’s current value.

## History

Do not add a template sensor that copies the reading into this check. The integration already avoids that.

Give a noisy sensor some hysteresis, and set **Hold** if a short spike should not change the verdict. Hold applies while Home Assistant is running. After a restart the current verdict is published immediately, so the board is not blank for the hold time.

The open-issue sensor has no statistics class, so it does not build long-term statistics.

## Not in this version

- Notifications, repeat, acknowledge, and a master “notify me” switch
- A Lovelace card that fills in `{value}` and `{min}`
- Import and export between installations

Those stay later on purpose. The entities from this version do not need to change for them.

## Tests

The verdict rules are plain Python. From the repository root:

```text
python3 -m unittest discover -s tests
```

Home Assistant is not required for that run.
