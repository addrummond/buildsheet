from lxml import etree as ET
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import math
import re
import sys

NORMAL_COLOR = colors.lightgrey
HIGHLIGHT_COLOR = colors.black

angle_re = re.compile(r"^(M?)R(\d+)(?:\.\d+)?$")
prefix_re = re.compile(r"\s*([A-Za-z]+)\s*\d+\s*$")

def rotate_coords(xy, angle):
    if angle is None:
        return xy
    angle = (angle/180)*math.pi
    x, y = xy
    x2 = (x * math.cos(angle)) - (y * math.sin(angle))
    y2 = (x * math.sin(angle)) + (y * math.cos(angle))
    return x2, y2

class BoardInfo():
    def __init__(self):
        self.value_to_components = { }
        self.name_to_component = { }
        self.layer_to_components = { }
        self.layer_value_to_components = { }
        self.components = [ ]

class Component():
    def __init__(self, x, y, name, prefix, value, pads, angle, layer):
        self.x = x
        self.y = y
        self.name = name
        self.prefix = prefix
        self.value = value
        self.pads = pads
        self.angle = angle
        self.layer = layer

class Pad():
    def __init__(self, x, y, width, height, angle):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.angle = angle

def getfloat(elem, name):
    f = elem.get(name)
    if f is None:
        raise Exception("Expecting attribute '%s'" % name)
    v = None
    try:
        v = float(f)
    except ValueError:
        raise Exception("Non-float value for attribute '%s'" % name)
    return v


def get_board_info(filename):
    bi = BoardInfo()

    tree = ET.parse(filename)
    root = tree.getroot()

    # Get the number of the dimension layer.
    dl = root.xpath("//layer[@name='Dimension']")
    if len(dl) == 0:
        raise Exception("Could not find Dimension layer def")
    bi.dimension_layer_number = dl[0].get('number', 20)

    # Get the numbers of the top and bottom layers.
    tl = root.xpath("//layer[@name='Top']")
    if (len(tl) == 0):
        raise Exception("Could not find Top layer def")
    bi.top_layer = tl[0].get('number', '1')
    bl = root.xpath("//layer[@name='Bottom']")
    if (len(bl) == 0):
        raise Exception("Could not find Bottom layer def")
    bi.bottom_layer = bl[0].get('number', '16')

    # Get the bounding rectangle from the dimension layer wires.
    ws = root.xpath("//wire[@layer='20']")
    if len(ws) < 2:
        raise Exception("Error processing dimension layer wires")
    xmin, ymin = float('+inf'), float('+inf')
    xmax, ymax = float('-inf'), float('-inf')
    for w in ws:
        xmin = min(xmin, getfloat(w, "x1"), getfloat(w, "x2"))
        ymin = min(ymin, getfloat(w, "y1"), getfloat(w, "y2"))
        xmax = max(xmax, getfloat(w, "x1"), getfloat(w, "x2"))
        ymax = max(ymax, getfloat(w, "y1"), getfloat(w, "y2"))
    bi.xmin = xmin
    bi.xmax = xmax
    bi.ymin = ymin
    bi.ymax = ymax
    bi.width = xmax - xmin
    bi.height = ymax - ymin

    # Get components.
    comps = root.xpath("//element")
    ourcomps = [ ]
    for c in comps:
        compname = c.get("name")
        if compname is None or compname == '':
            raise Exception("Component without name")

        compprefix = None
        m = re.match(prefix_re, compname)
        if m:
            compprefix = m.group(1)

        compvalue = c.get("value")
        if compvalue is None:
            raise Exception("Component without value")

        package_name = c.get("package")
        if package_name is None:
            raise Exception("Component without package")
        # Find the package.
        package = root.xpath("//package[@name='%s']" % package_name)
        if len(package) == 0:
            raise Exception("Could not find package")
        ppads = package[0].xpath("smd")
        if len(ppads) == 0:
            print("Skipping package '%s' (component '%s') since it has no pads" % (package_name, compname))
            continue

        compx = getfloat(c, "x")
        compy = getfloat(c, "y")

        rot = c.get("rot")
        eangle = None
        mirrored = False
        if rot is not None:
            m = re.match(angle_re, rot)
            if not m:
                raise Exception("Could not parse angle '%s'" % rot)
            eangle = float(m.group(2))
            mirrored = (m.group(1) == "M")

        ourpads = [ ]
        complayer = None # Assume that all component's pads will be on same layer.
        for p in ppads:
            if p.get("layer") is None:
                raise Exception("Could not get pad layer")

            if complayer is None:
                complayer = p.get("layer")
                if mirrored:
                    if complayer == bi.top_layer:
                        complayer = bi.bottom_layer
                    elif complayer == bi.bottom_layer:
                        complayer = bi.top_layer

            x = getfloat(p, "x")
            y = getfloat(p, "y")

            # Get angle.
            rot = p.get("rot")
            pangle = None
            if rot is not None:
                m = re.match(angle_re, rot)
                if not m:
                    raise Exception("Could not parse angle")
                pangle = float(m.group(2))

            op = Pad(x = x,
                     y = y,
                     width = getfloat(p, "dx"),
                     height = getfloat(p, "dy"),
                     angle = pangle)
            ourpads.append(op)

        com = Component(
            x = compx,
            y = compy,
            name=compname,
            prefix=compprefix,
            value=compvalue,
            pads=ourpads,
            angle=eangle,
            layer=complayer
        )
        ourcomps.append(com)

        bi.name_to_component[compname] = com
        if compvalue in bi.value_to_components:
            bi.value_to_components[compvalue].append(com)
        else:
            bi.value_to_components[compvalue] = [com]

        if complayer in bi.layer_to_components:
            bi.layer_to_components[complayer].append(com)
        else:
            bi.layer_to_components[complayer] = [com]

        if (complayer, compvalue) in bi.layer_value_to_components:
            bi.layer_value_to_components[(complayer, compvalue)].append(com)
        else:
            bi.layer_value_to_components[(complayer, compvalue)] = [com]

    bi.components = ourcomps
    return bi

def render_component_pad(cv, bi, c, p, highlight):
    corners = [(-(p.width/2), -(p.height/2)),
               ((-(p.width/2), (p.height/2))),
               ((p.width/2), (p.height/2)),
               ((p.width/2), -(p.height/2))]
    corners = [rotate_coords(cr, p.angle) for cr in corners]
    corners = [(cr[0] + p.x, cr[1] + p.y) for cr in corners]
    corners = [rotate_coords((cr[0], cr[1]), c.angle) for cr in corners]
    corners = [(cr[0] + c.x, cr[1] + c.y) for cr in corners]
    pth = cv.beginPath()
    pth.moveTo(corners[0][0] - bi.xmin, corners[0][1] - bi.ymin)
    for i in range(1, len(corners)):
        pth.lineTo(corners[i][0] - bi.xmin, corners[i][1] - bi.ymin)
    cv.setFillColor(HIGHLIGHT_COLOR if highlight else NORMAL_COLOR)
    cv.drawPath(pth, fill=1, stroke=0)

def render_components(cv, bi, all_cs, val_cs):
    for c in all_cs:
        if c in val_cs:
            continue
        for p in c.pads:
            render_component_pad(cv, bi, c, p, highlight=False)

    for c in val_cs:
        for p in c.pads:
            render_component_pad(cv, bi, c, p, highlight=True)

def layout_by_same_value(cv, bi, layer):
    headingspace = bi.height / 10.0
    cwidth = bi.width
    cheight = bi.height+headingspace

    cv.setPageSize((cwidth, cheight))

    values = bi.value_to_components.keys()
    for val in values:
        val_cs = bi.layer_value_to_components.get((layer, val))
        if val_cs is None:
            continue

        prefixes = { }
        for vc in val_cs:
            if vc.prefix in prefixes:
                prefixes[vc.prefix].append(vc)
            else:
                prefixes[vc.prefix] = [vc]

        all_cs = bi.layer_to_components.get(layer, [])

        for prefix_cs in prefixes.values():
            names = [c.name for c in prefix_cs]
            names.sort()

            cv.setFont("Helvetica", headingspace/5.0)
            cv.drawCentredString(cwidth/2, cheight-(headingspace/2.0), "V = %s, N = %s" % (val, ','.join(names)))
            render_components(cv, bi, all_cs, val_cs)
            cv.showPage()

if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.stderr.write("\nUsage:\n\n")
        sys.stderr.write("    python3 buildsheet.py board.brd [layer] out.pdf\n\n")
        sys.stderr.write("Where [layer] is a layer number or 'top' or 'bottom'.\n\n")
        sys.exit(1)

    board_file = sys.argv[1]
    layer = sys.argv[2]
    output_file = sys.argv[3]

    bi = get_board_info(board_file)

    if layer.lower() == "top":
        layer = bi.top_layer
    elif layer.lower() == "bottom":
        layer = bi.bottom_layer

    print("Width %f, height %f" % (bi.width, bi.height))
    cv = canvas.Canvas(output_file)
    layout_by_same_value(cv, bi, layer)
    cv.save()
