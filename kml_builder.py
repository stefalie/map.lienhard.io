from datetime import date
import base64
import gpxpy
import json
import re
import shutil

# TODO: Create s separate kml file for every year.
# TODO: gpx optimize

input_json_file = "outings.json"
ouptut_kml_file = "map.lienhard.io.kml"
kml_title = "Cheryl &amp; Stefan's Outings"

line_width = 8
marker_icon_size = 40

# "Borrow" the colors (except alpha) from the SAC Tourenportal.
alpha = 0.8
styles = {
        #"hut"         : (227,   6,  19, alpha),
        "hike"        : ( 35, 113,   0, alpha),
        "hochtour"    : (102,  45, 145, alpha),
        "climb"       : (255,  61,  18, alpha),
        "viaferrata"  : (255, 136,   0, alpha),
        "skitour"     : (  0,  51, 255, alpha),
        #"snowshoetour": (  0, 138, 121, alpha)
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
		<name>{title}</name>
{styles}
{placemarks}
	</Document>
</kml>
'''.strip()
template_style = '''
<Style id="{name}">
	<LineStyle>
		<color>{color}</color>
		<width>{width}</width>
	</LineStyle>
	<IconStyle>
		<Icon>
			<href>{icon_url}</href>
		</Icon>
	</IconStyle>
</Style>
'''.strip()
# NOTE: We don't use the <name> tag for <Placemark>s because geo admin will
# dsipaly it on the map for <Point>s (not for <LineString>). I don't know how
# to hide it. Instead we put the name/title into the description.
template_placemark = '''
<Placemark>
	<styleUrl>#{activity_type}</styleUrl>
	<description><![CDATA[<h4>{title}</h4><p><time datetime="{date}">{date_formatted}</time></p>{description}]]></description>
{geometry}
</Placemark>
'''.strip()
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
    return template_style.format(name=style[0], color=kml_hex_color(style[1]), width=line_width, icon_url=svg_base64_data_url(style[1]))
kml_styles = "\n".join(map(generate_style, styles.items()))

def generate_placemark(outing):
    num_points = len(outing["points"]) if ("points" in outing) else 0
    num_tracks = len(outing["tracks"]) if ("tracks" in outing) else 0

    date_fmt = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"  # TODO: Could be more restrictive.
    assert((("title" in outing) and (len(outing["title"]) > 0)) and
           (("type" in outing) and (outing["type"] in styles)) and
           (("date" in outing) and re.search(date_fmt, outing["date"]))), f"Every outing needs a valid title, type, and date: {outing}"
    assert (num_points + num_tracks) > 0, f"An outing needs at least one track or point: {outing}"

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
            with open(track, "r", encoding="utf8") as gpx_file:
                gpx = gpxpy.parse(gpx_file)
            assert((len(gpx.tracks) == 1) and
                   (len(gpx.waypoints) == 0) and
                   (len(gpx.routes) == 0)), "We expect exactly 1 track per gpx file."

            # Concat all points in all segments
            all_points = [p for s in gpx.tracks[0].segments for p in s.points]
            coords = " ".join(map(lambda p : template_coordinate.format(long=p.longitude, lat=p.latitude), all_points))
            return template_linestring.format(coordinates=coords)
        geom += "\n".join(map(generate_linestring, outing["tracks"]))

    if (num_points + num_tracks) > 1:
        geom = indent(geom, 1)
        geom = template_multigeometry.format(geometries=geom)
    geom = indent(geom, 1)

    desc = ""
    # TODO: Validate URLs.
    if ("stravaUrl" in outing):
        desc += f'<p><a href="{outing["stravaUrl"]}">See tracks on Strava outing</a></p>'
    if ("photoUrl" in outing):
        desc += f'<p><a href="{outing["photoUrl"]}">See photos</a></p>'
    if ("note" in outing):
        desc += f"<p>{outing['note']}</p>"
    d = date(int(outing["date"][:4]), int(outing["date"][5:7]), int(outing["date"][8:10]))
    d = d.strftime("%B %d, %Y").replace(" 0", " ")
    return template_placemark.format(activity_type=outing["type"], title=outing["title"], date=outing["date"], date_formatted=d, description=desc, geometry=geom)
kml_placemarks = "\n".join(map(generate_placemark, outings))

kml_styles = indent(kml_styles, 2)
kml_placemarks = indent(kml_placemarks, 2)
kml_all = template_body.format(title=kml_title, styles=kml_styles, placemarks=kml_placemarks)

with open(ouptut_kml_file, "w", encoding="utf8") as out_file:
    out_file.write(kml_all)
