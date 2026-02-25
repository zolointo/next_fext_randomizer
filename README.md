Next Fest is about my favouritest time(s) of year. I'm always challenged with how to navigate all of that content and pay attention to both the big and the small.

Steam's interface caters to your algorithm. SteamDB is a bit better, but is perhaps a bit noisy.

With this Fest's game count being around 3400, I decided to work on an efficient way to get wide exposure:

- I pulled the list of appids from SteamDB's Next Fest page and sorted them randomly
- I plugged that list into a Python processor, which was generated through Claude ai
- It outputs groups of 100 into an HTML page with just the name of the game, the link to the game, and the trailer that activates on mouse-over

![screenshot](https://github.com/zolointo/next_fext_randomizer/blob/main/next_fest_randomizer_example.png)
