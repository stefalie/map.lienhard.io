from datetime import date
import base64
import gpxpy
import numpy as np
import json
import rdp
import re

# Required Python packages:
# - gpxpy
# - rdp

# TODO: Create s separate kml file for every year (or have a separate .json per year).
# TODO: Validate URLs to photos and tracks.
# TODO: Gpx optimization alternative: https://github.com/Alezy80/gpx_reduce
# TODO: Potentially extra Strava activity numbers from file names (maybe not for
# now, the gpx files could also come from elsewhere than Strava).
# TODO: Make sure that activity dates match their photo gallery dates (as in they cannot
# be too far apart, an exact match is not always possible as galleries sometimes cover
# longer outings).

kml_title = "Cheryl &amp; Stefan's Outings"
input_json_file = "outings.json"
ouptut_kml_file = "map.lienhard.io.kml"

gpx_data_dir = "data/"
photo_base_url = "https://stefalie.smugmug.com/"
strava_base_url = "https://www.strava.com/activities/"

rdp_epsilon = 0.0002

line_width = 8
marker_icon_size = 48

# Tricky to find colors that stand out against the map background at all zoom
# levels.
alpha = 0.9
styles = {
        "Hike"       : (255,   0,   0, alpha),  # Red
        "ViaFerrata" : (255,   0,   0, alpha),  # Red (same as Hike)
        "Hochtour"   : (139,   0, 139, alpha),  # DarkMagenta 
        "Climb"      : (139,   0, 139, alpha),  # DarkMagenta (same as Houchtour)
        "Skitour"    : (  0,   0, 255, alpha),  # Blue
        "Run"        : ( 55, 180,   0, alpha),  # Green
        "Bike"       : (255, 136,   0, alpha),  # Orange
        "XC-Ski"     : (  0, 227, 216, alpha),  # Turqoise
}

with open(input_json_file, "r", encoding="utf8") as in_file:
    outings = json.load(in_file)

def indent(template_str, indent):
    lines = template_str.split("\n")
    lines = map(lambda x : (indent * "\t") + x, lines)
    return "\n".join(lines)

template_body = '''
<?xml version="1.0" encoding="utf-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
	<Document>
		<name>{kml_title}</name>
{styles}
{placemarks}
	</Document>
</kml>
'''.strip()
template_style = '''
<Style id="{style_name}">
	<LineStyle>
		<color>{color}</color>
		<width>{width}</width>
	</LineStyle>
	<IconStyle>
		<Icon>
			<href>{icon_url}</href>
		</Icon>
		<hotSpot x="0.5" y="0.0" xunits="fraction" yunits="fraction"/>
	</IconStyle>
</Style>
'''.strip()
# NOTE: We don't use the <name> tag for <Placemark>s because geo admin will
# dsipaly it on the map for <Point>s (not for <LineString>). I don't know how
# to hide it. Instead we put the name/title into the description.
template_placemark = '''
<Placemark>
	<styleUrl>#{style_name}</styleUrl>
	<description><![CDATA[{description}]]></description>
{geometry}
</Placemark>
'''.strip()
# NOTE: <MultiGeometry> is suboptimal for geo admin for <LineString>s as it
# disables the display for the elevation profile.
template_multigeometry = '''
<MultiGeometry>
{geometries}
</MultiGeometry>
'''.strip()
template_coordinate = "{long:.5f},{lat:.5f}"
template_point = '''
<Point>
	<altitudeMode>clampToGround</altitudeMode>
	<coordinates>{coordinate}</coordinates>
</Point>
'''.strip()
template_linestring = '''
<LineString>
	<altitudeMode>clampToGround</altitudeMode>
	<tessellate>1</tessellate>
	<coordinates>{coordinates}</coordinates>
</LineString>
'''.strip()

# Icon marker
# Copied base64 data from https://thenounproject.com/term/map-marker/5624/ and
# passed it through https://jakearchibald.github.io/svgomg/.
# Unfortunately the <color> tags inside <IconStyle> seem to be ignored on geo
# admin, therefore we replicate the svg icon for the different fill colors.
marker_icon_svg = '<svg height="{size}" width="{size}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="rgba({r},{g},{b},{a})" d="M50 14a24 24 0 00-24 24c0 13.3 24 48 24 48s24-34.8 24-48a24 24 0 00-24-24zm0 36.5a12 12 0 110-24 12 12 0 010 24z"/></svg>'

def svg_base64_data_url(color):
    marker_icon_svg_filled = marker_icon_svg.format(size=marker_icon_size, r=color[0], g=color[1], b=color[2], a=color[3])
    marker_icon_b64 = base64.b64encode(marker_icon_svg_filled.encode("ascii"))
    return f"data:image/svg+xml;base64,{marker_icon_b64.decode('ascii')}"

def kml_hex_color(c):
    # aabbggrr
    return f"{int(c[3]*255):0{2}X}{c[2]:0{2}X}{c[1]:0{2}X}{c[0]:0{2}X}"

def generate_style(style):
    return template_style.format(style_name=style[0], color=kml_hex_color(style[1]), width=line_width, icon_url=svg_base64_data_url(style[1]))
kml_styles = "\n".join(map(generate_style, styles.items()))

date_fmt = "([0-9]{4}-[0-9]{2}-[0-9]{2})"  # TODO: Could be more restrictive.
type_fmt = "(" + "|".join(styles) + ")"
title_fmt = "(\S+?)"  # Anything but whitespace
multi_track_fmt = "(?:__(\d))?"
track_file_format = f"^{date_fmt}__{type_fmt}__{title_fmt}{multi_track_fmt}.gpx$"

# Taking from https://github.com/Andrii-D/optimize-gpx/blob/master/optimize-gpx.py
# but without splitting segments and without elevation correction (shouldn't be
# necessary for the data from Garmin/Strava).
def optimize_segment_rdp(seg):
    result = gpxpy.gpx.GPXTrackSegment()
    arr = np.array(list(map(lambda p: [p.latitude, p.longitude], seg.points)))
    mask = rdp.rdp(arr, algo="iter", return_mask=True, epsilon=rdp_epsilon)
    parr = np.array(seg.points)
    result.points = list(parr[mask])
    return result

# Put elements that we visited already into sets to check that we don't have duplicates.
encountered_titles = set()
encountered_dates = set()
encountered_strava_urls = set()

def generate_placemark(outing):
    num_points = len(outing["points"]) if ("points" in outing) else 0
    num_tracks = len(outing["tracks"]) if ("tracks" in outing) else 0

    # Extract date, type, and title from the first available track.
    if num_tracks > 0:
        matches = re.search(track_file_format, outing["tracks"][0])
        assert(matches), f'Gpx file name cannot be regex matched: {outing["tracks"][0]}'
        assert(len(matches.groups()) == 4 or len(matches.groups()) == 5), f'Gpx file format incorrect: {outing["tracks"][0]}'
        date_str = matches.group(1)
        activity_type = matches.group(2)
        title = matches.group(3)

        # Make sure multi tracks are named the same way.
        if num_tracks > 1:
            assert matches.group(4), f'Wrong multi track format: {outing["tracks"][0]}'
        if matches.group(4):
            assert(matches.group(4) == "1"), f'The first multi track files must have a "__1" suffix: {outing["tracks"]}'
            for i, out in enumerate(outing["tracks"]):
                assert(out == f"{date_str}__{activity_type}__{title}__{i + 1}.gpx"), f"Wrong multi track format: {out}"

        title = title.replace("_", " ")


    # Overwrite date, type, or title if explicitly specified
    if "date" in outing:
        date_str = outing["date"]
    if "type" in outing:
        activity_type = outing["type"]
    if "title" in outing:
        title = outing["title"]

    if title in encountered_titles:
        print(f"NOTE: Already encountered title: {title}")
    if date_str in encountered_dates:
        print(f"NOTE: Already encountered date: {date_str}")
    encountered_titles.add(title)
    encountered_dates.add(date_str)

    assert((len(title) > 0) and (activity_type in styles) and re.search(f"^{date_fmt}$", date_str)), f"Every outing needs a valid title, type, and date: {outing}"
    assert (num_points + num_tracks) > 0, f"An outing needs at least one track or point: {outing}"

    # Title, type, and date
    # NOTE: A <time> tag unfortunately gets not shown in the popover in the
    # non-<iframe> version.
    date_formatted = date(int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10]))
    date_formatted = date_formatted.strftime("%B %d, %Y").replace(" 0", " ")
    desc = f'<h4>{title}</h4><p>{activity_type} on {date_formatted}.</p>'

    # Photo links
    if ("photoUrl" in outing):
        photo_url = outing["photoUrl"]
        desc += f'<p>See <a href="{photo_base_url}{photo_url}" target="_blank">photos</a>.</p>'

    # Strava links
    if ("stravaUrl" in outing):
        strava_urls = outing["stravaUrl"]
        num_urls = len(strava_urls)
        assert(num_urls > 0), f"A 'stravaUrl' entry cannot be empty: {outing}"
        for url in strava_urls:
            assert(url not in encountered_strava_urls), f"Already encountered Strava URL: {url}"
            encountered_strava_urls.add(url)

        if num_urls > 1:
            strava_links = ", ".join(map(lambda i_url : f'<a href="{strava_base_url}{i_url[1]}" target="_blank">tracks {i_url[0] + 1}</a>', enumerate(strava_urls)))
        else:
            strava_links = f'<a href="{strava_base_url}{strava_urls[0]}" target="_blank">tracks</a>'
        desc += f"<p>See tracks on Strava: {strava_links}.</p>"

    # Notes
    if ("note" in outing):
        desc += f"<p>{outing['note']}</p>"

    geom = ""
    if num_points > 0:
        def generate_point(point):
            assert((("lat" in point) and (type(point["lat"]) == float)) and
                   (("long" in point) and (type(point["long"]) == float)) and
                   (len(point) == 2)), f"Every point needs exactly 'lat' and 'long': {point}"
            coord = template_coordinate.format(long=point["long"], lat=point["lat"])
            return template_point.format(coordinate=coord)
        geom += "\n".join(map(generate_point, outing["points"]))

    if num_tracks > 0:
        def generate_linestring(track):
            gpx_file_name = gpx_data_dir + track
            with open(gpx_file_name, "r", encoding="utf8") as gpx_file:
                gpx = gpxpy.parse(gpx_file)
            assert((len(gpx.tracks) == 1) and
                   (len(gpx.waypoints) == 0) and
                   (len(gpx.routes) == 0)), f"We expect exactly 1 track per gpx file: {gpx_file_name}"

            # Concat all points in all segments
            assert(len(gpx.tracks[0].segments) == 1)
            #all_points = [p for s in gpx.tracks[0].segments for p in s.points]
            all_points = optimize_segment_rdp(gpx.tracks[0].segments[0]).points
            coords = " ".join(map(lambda p : template_coordinate.format(long=p.longitude, lat=p.latitude), all_points))
            return template_linestring.format(coordinates=coords)
        geom += "\n".join(map(generate_linestring, outing["tracks"]))

    if (num_points + num_tracks) > 1:
        geom = indent(geom, 1)
        geom = template_multigeometry.format(geometries=geom)
    geom = indent(geom, 1)

    return template_placemark.format(style_name=activity_type, description=desc, geometry=geom)

try:
    kml_placemarks = "\n".join(map(generate_placemark, outings))

    kml_styles = indent(kml_styles, 2)
    kml_placemarks = indent(kml_placemarks, 2)
    kml_all = template_body.format(kml_title=kml_title, styles=kml_styles, placemarks=kml_placemarks)

    with open(ouptut_kml_file, "w", encoding="utf8") as out_file:
        out_file.write(kml_all)
except AssertionError as error_msg:
    print("ERROR: " + error_msg)
    print("Aborting ...")

