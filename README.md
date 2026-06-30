# EDOPro Standard/Rush HD Pics Downloader
Warning! AI Slop! But it works! <br>
Tested only on Windows. Linux and macOS might might be buggy. 

This is a simple HD picture downloader for EDOPro.
It can download:
- Standard card pictures
- Rush Duel card pictures
- cropped Field Spell pictures
## Easy Install
Put the files in your EDOPro folder, where EDOPro.exe is. Do not put them inside the `pics` folder.
1. Use the "install-python-dependencies" file for your operating system or [install Python 3.12 manually](https://www.python.org/downloads/).
    - The downloader.py also uses `tkinter`, which is the normal Python window/GUI system. On Windows it usually comes with Python. On Linux/macOS it may need to be installed separately.

2. On Windows, double-click the `EDOPro-Standard-Rush-HD-Pics-Downloader.py` file to run it. Only works if Python is installed.

> [!WARNING]
> Yugipedia may occasionally be unstable. This usually only affect Rush cards downloads.<br>
> If some images fail to download, wait a few hours and try again before reporting a bug.<br>
> If "**Skip same size or larger**" is enabled, it will mostly skip previously downloaded ones and do the ones that failed.<br>

## Main Options

### Standard cards
Downloads normal Yu-Gi-Oh! card pictures from YGOPRODeck.

### Rush cards
Downloads Rush Duel card pictures.

### Output
Choose the file type:
- `JPG`
- `PNG`

Choose the size:
- `Full quality` (2.85GB for 18k pics)
- `443x640` (1.21GB for 18k pics)
- `421x614`

The fixed sizes do not make small pictures bigger. They only shrink larger pictures when needed.

### Force Overwrite Existing
Download again even if the picture already exists.

### Delete old opposite format first
If you download JPG, it can delete the old PNG.
If you download PNG, it can delete the old JPG.

### Skip if current image is same size or larger
For fixed-size downloads, this skips pictures that are already big enough.
Note: Some YGOProDeck images are at only 421x614 or lower, so they will always download at those sizes. 

## Rush Picture Sources
Rush cards are tried in this order:
1. Rush-HD GitHub
2. YGOPRODeck (usually skipped)
3. Yugipedia

If one source does not have the picture, the downloader tries the next one.

## Field Spell Pictures

The downloader can also download cropped Field Spell pictures.
These are saved here:
```text
pics/field/
```
If the normal source does not have a field picture, the downloader can try the included Fields GitHub fallback.

## Beta Option
`Match duplicate Rush fallback rarities (beta)` is turned on by default.
Some Rush cards have the same name but different card IDs. This option tries to pick a better Yugipedia picture for those duplicate cards.
It does not change pictures that were already found on Rush-HD.
Because this is based on guessing from card ID order and Yugipedia gallery data, it may not be perfect for every card.

## Reports

The downloader only creates a report when there is something useful to report, such as:
- missing pictures
- errors
- field picture problems
- Yugipedia retry details
- duplicate Rush card details, if that report option is turned on

Reports are saved in the EDOPro folder, for example:
```text
EDOPro-HD-Pics-Download-Report-20260630-123456.txt
```

## Settings
The downloader saves your options in this file:
```text
EDOPro-Standard-Rush-HD-Pics-Downloader.ini
```
You do not need to create this file yourself.

## Check App Updates
The `Check App Updates` button checks if there is a newer version of this downloader.
It does not check for new card pictures.

## Notes
- Python Pillow is needed for resizing and converting images.
- If you only download full-quality pictures in the same format, some downloads can work without Pillow.
- On Linux, Python tkinter may need a separate package.
- On macOS, Homebrew users may need `python-tk@3.12`.

**Linux** Ubuntu/Debian-based (might not work on some distros):
```sh
sudo apt update
sudo apt install python3.12 python3.12-tk python3.12-venv python3-pip
python3.12 -m pip install --user --upgrade Pillow
```

**macOS**
Using Homebrew:
```sh
brew install python@3.12 python-tk@3.12
python3.12 -m pip install --upgrade Pillow
```

## Credits
Special thanks to:
- [Armytille/EDOPro-HD-Pics-Downloader](https://github.com/Armytille/EDOPro-HD-Pics-Downloader), which this project is based on.
- [Yoshi80/Rush-HD-Pictures](https://github.com/Yoshi80/Rush-HD-Pictures), which provides the Rush Duel HD pictures used by this downloader.

Additional data and images may come from:
- [YGOPRODeck](https://ygoprodeck.com/)
- [Yugipedia](https://yugipedia.com/)
