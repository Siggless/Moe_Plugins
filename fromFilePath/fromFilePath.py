import moe
from moe.library import Track, LibItem

import datetime
import logging
import mediafile
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("moe.fromFilePath")


_NULL_TRACK_FIELDS = {
    "title" : "<Unknown Track>",
    "track_num" : -1,    # Can't be 0 as that still counts as None for int type
    "disc" : 1
}
_NULL_ALBUM_FIELDS = {
    "title" : "Unknown Album",
    "artist" : "Unknown Artist",
    "date" : datetime.date.min,
    "disc_total" : 1
}
_null_track_num_count = 0   # Make track_num unique to prevent them being classed as duplicates
_last_added_album:str = ""  # Try to restart unique negative track_num for each separate album
"""
This track_num thing has turned into bodge on-top-of bodge...
If blank we first set to fake null value -1
If still -1 after guessing from filename, we set to a sequential negative value so that
 these don't get flagged as duplicates with other track_num -1 tracks within an album.
Before the write we set any negatives back to 0 because the tagger errors on negatives
 on certain file types like m4a.
After the write we go and clear the track_nums of 0 again.
"""

_GUESS_TAGS = True
_PATTERNS = [
    r"^(?P<disc>\d+)[\.\-_:]+(?P<track_num>\d+)\s+(?P<title>.+)$",
    r"^(?P<track_num>\d+)\s+(?P<title>.+)$"
]
"""
The list of Regex patterns for the file name. Sorted by priority (the first match is used).
 The Match names must match the fields they will populate.
 Assumes that the first whitespace separates the track and title, and no file extension.
E.g. \"01-02 My Track Name\" returns disc \"01\", track_num \"02\", title \"My Track Name\".
"""

def guess_fields(path:Path) -> dict[str,Any]:
    """Populates a field dictionary with fields guessed from the file path.
    The file name is matched using the Regex patterns to get the fields.
    The parent directory is the album title since this is how MOE expects it when adding.
    """
    filename = path.stem
    fields:dict = {
        "title" : filename,
        "album_title" : path.parent.name
    }  # Default to just the full names as the titles
    
    for pattern in _PATTERNS:
        match = re.match(pattern, filename)
        if match:
            matchDict = match.groupdict()
            for i in matchDict:
                fields[i] = match[i]
            break
    
    log.debug(f"Fields guessed from path: {fields}")
    return fields


@moe.hookimpl
def read_custom_tags(track_path:Path, album_fields:dict[str, Any], track_fields:dict[str, Any]):
    """The hooked function is called when reading the fields from the track file.
    We use this to populate blank fields with information guessed from the file path.
    We also set required fields that would still be null after that to our "fake null" values,
     so that we can skip writing them to the file's tags later, but still be added to the DB.
    """
    log.debug(f"fromFilePath adding {track_path.parent.name} - {track_path.name}")
    
    if _GUESS_TAGS:
        guessed_fields = guess_fields(track_path)
    
    # Fixup blanks with fake null fields or file path guesses
    for key in _NULL_TRACK_FIELDS:
        if not track_fields[key]:
            track_fields[key] = _NULL_TRACK_FIELDS[key]
            if _GUESS_TAGS and key in guessed_fields:
                if type(_NULL_TRACK_FIELDS[key]) is int:
                    track_fields[key] = int(guessed_fields[key])    # Cast to int just in case
                else:
                    track_fields[key] = guessed_fields[key]
                log.debug(f"\tSetting {key} from file name: {str(track_fields[key])}")
            else:
                log.debug(f"\tNo {key} so setting to {str(track_fields[key])}")
    
    for key in _NULL_ALBUM_FIELDS:
        if not album_fields[key]:
            album_fields[key] = _NULL_ALBUM_FIELDS[key]
            guessed_key = "album_" + key
            if _GUESS_TAGS and guessed_key in guessed_fields:
                album_fields[key] = guessed_fields[guessed_key]
                log.debug(f"\tSetting {guessed_key} from file name: {str(album_fields[key])}")
            else:
                log.debug(f"\tNo {guessed_key} so setting to {str(album_fields[key])}")
                
    # Specifically for track ID, set negatives to separate values within an album,
    #  so that they aren't marked as duplicates in the database. Yes this is bodgey.
    global _last_added_album, _null_track_num_count
    if track_fields["track_num"] == _NULL_TRACK_FIELDS["track_num"]:
        if _last_added_album != album_fields["title"]:
            _null_track_num_count = 1
            _last_added_album = album_fields["title"]
        else:
            _null_track_num_count = _null_track_num_count + 1
        track_fields["track_num"] = -_null_track_num_count
        log.debug(f"\tChanging track_num to {str(track_fields["track_num"])}")


@moe.hookimpl
def process_new_items(items: list[LibItem]):
    """We set any negative track_nums to 0 to prevent the default write errors
     on file types that don't support negatives, i.e. m4a.
    """
    for item in items:
        if isinstance(item, Track):
            if item.track_num < 0:
                item.track_num = 0
                
@moe.hookimpl
def process_changed_items(items: list[LibItem]):
    """We set any negative track_nums to 0 to prevent the default write errors
     on file types that don't support negatives, i.e. m4a.
    """
    for item in items:
        if isinstance(item, Track):
            if item.track_num < 0:
                item.track_num = 0

@moe.hookimpl
def write_custom_tags(track: Track):
    """The hooked function is called when tags are written to a track's file,
     after the default writing is performed.
    We use this to DELETE specific tags that we set to our "fake null" values on read.
    """
    
    log.debug(f"fromFilePath writing tags for {track.path}")
    
    audio_file = mediafile.MediaFile(track.path)
    # Setting numeric tags like track_num to None still saves them as 0,
    #  so we will use MediaData.update() here instead of setting directly.
    nullTags = {}
    
    # track_num specifically has our DB duplicate bodge to any negative int
    if track.track_num <= 0 or audio_file.track <= 0:   #_NULL_TRACK_FIELDS["track_num"]:
        log.debug('\tDeleting track_num tag ' + str(audio_file.track))
        nullTags["track"] = None
        #audio_file.track=None
        
    if audio_file.date == _NULL_ALBUM_FIELDS["date"]:
        log.debug('\tDeleting date tag ' + str(audio_file.date))
        nullTags["date"]=None
        #audio_file.date=None
        
    audio_file.update(nullTags)
    audio_file.save()
    
