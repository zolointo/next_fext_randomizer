# Next Fest Randomizer

## Description
Creates a two-column table containing a Steam widget and video for each Steam AppID that is supplied.

![screenshot](https://github.com/zolointo/next_fext_randomizer/blob/main/documentation_screenshots/next_fest_randomizer_example.png)

## Required modules
- requests
- playwright

## Use
### Gather Steam AppIDs
First, you'll need to gather appids for the apps that you want to generate a page for.

You can extract this from the URL for the game on Steampowered.com or SteamDB.info:
![screenshot](https://github.com/zolointo/next_fext_randomizer/blob/main/documentation_screenshots/steam_url_with_appid.png)
![screenshot](https://github.com/zolointo/next_fext_randomizer/blob/main/documentation_screenshots/steamdb_url_with_appid.png)


You can also find this at the top of the SteamDB.info page for the game
![screenshot](https://github.com/zolointo/next_fext_randomizer/blob/main/documentation_screenshots/steamdb_page_appid.png)

### Place AppIDs in steam_appids.txt
The AppIDs can be placed into steam_appids.txt in multiple ways
- a single line containing all AppIDs separated by spaces
- a single line containing all AppIDs separated by commas
- multiple lines containing an AppID, ending with a space
- multiple lines containing an AppID, ending with a comma

... or a combination of the above elements. The script is flexible!

Alternatively, you can
- run next_fest_randomizer.py via command line, and supply each AppID with a space between
- insert a comma separated list directly into next_fest_randomizer.py in the HARDCODED_APPIDS list

### Run
With your AppIDs setup, run the script.

If you have a large list of AppIDs, you **will** hit Steam's query rate limit. The script compensates for this with pauses.

For example, the list of 3400 games for Next Fest Feb 2026 took over an hour to complete.

### Done
The result is a series of html files named 'rando_bin_X.html'.

2026-02-28 - The 'randomization' is not built into the script at this point. If you do want to rando-sort the AppIDs, you'll have to do that external to thie script and then insert that list into the steam_appids.txt file.

## Why?
Next Fest is about my favouritest time(s) of year. I'm always challenged with how to navigate all of that content and pay attention to both the big and the small.

With this Fest's game count being around 3400, I decided to work on an efficient way to get wide exposure:

- I pulled the list of appids from SteamDB's Next Fest page and sorted them randomly
- I plugged that list into a Python processor, which was generated through Claude ai
- It outputs groups of 100 into an HTML page with just the name of the game, the link to the game, and the trailer that activates on mouse-over
