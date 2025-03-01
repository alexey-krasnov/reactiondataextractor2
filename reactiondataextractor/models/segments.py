# -*- coding: utf-8 -*-
"""
Segments
=======
Models created to identify different regions of a chemical schematic diagram.
Module expanded by :-
author: Damian Wilary
email: dmw51@cam.ac.uk
Previous adaptation:-
author: Ed Beard
email: ejb207@cam.ac.uk
and
author: Matthew Swain
email: m.swain@me.com
"""


from __future__ import absolute_import
from __future__ import division

from collections.abc import Sequence
from collections.abc import Collection
import numpy as np
from enum import Enum
from functools import wraps
import logging
from typing import List, Union, Tuple

import cv2
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from reactiondataextractor.configs import figure, ProcessorConfig
from .geometry import Line, Point

log = logging.getLogger('extract.segments')


def coords_deco(cls):
    """Decorator allowing accessing coordinates of panels directly from objects that have ``panel`` attributes"""
    for coord in ['left', 'right', 'top', 'bottom']:
        def fget(self, coordinate=coord):
            panel = getattr(self, 'panel')
            return getattr(panel, coordinate)
        prop = property(fget)
        setattr(cls, coord, prop)

    @wraps(cls)
    def wrapper(*args, **kwargs):
        return cls(*args, **kwargs)

    return wrapper


class FigureRoleEnum(Enum):
    """
    Enum used to mark connected components in a figure. Each connected component is assigned a role in a form of an
    enum member to facilitate segmentation.
    """
    ARROW = 1
    CONDITIONSCHAR = 2
    SUPERATOMCHAR = 3
    LABELCHAR = 4
    DIAGRAMPRIOR = 5
    DIAGRAMPART = 6   # Either a solitary bond-line (e.g. double bond) ar a superatom labels
    BONDLINE = 7
    OTHER = 8
    TINY = 9   # Used for tiny ccs that have not been assigned (noise or small dots)


class ReactionRoleEnum(Enum):
    """
    Enum used to mark panels (sometimes composed from a set of dilated connected components) in a figure.
    Original ccs are well described using the above ``FigureRoleEnum`` and hence this enum is only used for panels in
    (or coming from) dilated figure - in particular, to describe which structures are reactants and products,
    and which form part of the conditions region. ``ARROW`` and ``LABEL`` describe (if needed) corresponding
    dilated arrows and labels regions
    """
    ARROW = 1
    CONDITIONS = 2
    LABEL = 4
    GENERIC_STRUCTURE_DIAGRAM = 5
    STEP_REACTANT = 9
    STEP_PRODUCT = 10


class PanelMethodsMixin:
    """A mixin class used to directly access panel attributes inside more complex objects.
    Used for backward compatibility."""
    @property
    def center(self):
        return self.panel.center

    @property
    def left(self):
        return self.panel.left

    @property
    def right(self):
        return self.panel.right

    @property
    def top(self):
        return self.panel.top

    @property
    def bottom(self):
        return self.panel.bottom

    @property
    def area(self):
        return self.panel.area

    @property
    def height(self):
        return self.panel.height

    @property
    def width(self):
        return self.panel.width

    def center_separation(self, obj2):
        return self.panel.center_separation(obj2)

    def edge_separation(self, obj2):
        return self.panel.edge_separation(obj2)

    def contains(self, other):
        if hasattr(other, 'panel'):
            other = other.panel
        return self.panel.contains(other)

    def __iter__(self):
        return iter(self.panel)


class Rect(object):
    """
    A rectangular region.
    Base class for all panels.
    """

    @classmethod
    def create_megarect(cls, boxes: List['Rect']):
        """
        Creates a large rectangle out of all constituent boxes (rectangles containing connected components)
        :param iterable boxes: list of bounding boxes to combine into a larger box
        :return: a large rectangle covering all smaller rectangles
        """
        # print('boxes:', boxes)
        top = min(rect.top for rect in boxes)
        bottom = max(rect.bottom for rect in boxes)
        left = min(rect.left for rect in boxes)
        right = max(rect.right for rect in boxes)

        megabox = cls((top, left, bottom, right))
        return megabox

    def  __init__(self, coords):
        """
        :param (int, int, int, int): (top, left, bottom, right) coordinates of top-left and bottom-right rectangle points
        """
        self.coords = list(coords)


    @property
    def top(self):
        return self.coords[0]

    @top.setter
    def top(self, value):
        self.coords[0] = value

    @property
    def left(self):
        return self.coords[1]

    @left.setter
    def left(self, value):
        self.coords[1] = value

    @property
    def bottom(self):
        return self.coords[2]

    @bottom.setter
    def bottom(self, value):
        self.coords[2] = value

    @property
    def right(self):
        return self.coords[3]

    @right.setter
    def right(self, value):
        self.coords[3] = value

    @property
    def width(self):
        """Return width of rectangle in pixels. May be floating point value.
        :rtype: int
        """
        return self.right - self.left

    @property
    def height(self):
        """Return height of rectangle in pixels. May be floating point value.
        :rtype: int
        """
        return self.bottom - self.top

    @property
    def aspect_ratio(self):
        """
        Returns aspect ratio of a rectangle.
        :rtype : float
        """
        return self.width / self.height

    @property
    def perimeter(self):
        """Return length of the perimeter around rectangle.
        :rtype: int
        """
        return (2 * self.height) + (2 * self.width)

    @property
    def area(self):
        """Return area of rectangle in pixels. May be floating point values.
        :rtype: int
        """
        return self.width * self.height

    @property
    def diagonal_length(self):
        """
        Return the length of diagonal of a connected component as a float.
        """
        return np.hypot(self.height, self.width)

    @property
    def center(self):
        """Center point of rectangle. May be floating point values.
        :rtype: tuple(int|float, int|float)
        """
        xcenter = (self.left + self.right) / 2 if self.left is not None and self.right else None
        ycenter = (self.bottom + self.top) / 2
        return xcenter, ycenter

    @property
    def geometric_centre(self):
        """(x, y) coordinates of pixel nearest to center point.
        :rtype: tuple(int, int)
        """
        xcenter, ycenter = self.center
        return int(np.around(xcenter)), int(np.around(ycenter))

    def __repr__(self):
        return '%s(top=%s, left=%s, bottom=%s, right=%s)' % (
            self.__class__.__name__, self.top, self.left, self.bottom, self.right
        )

    def __str__(self):
        return '<%s (%s, %s, %s, %s)>' % (self.__class__.__name__, self.top, self.left, self.bottom, self.right)

    def __eq__(self, other):
        if self.left == other.left and self.right == other.right \
                and self.top == other.top and self.bottom == other.bottom:
            return True
        else:
            return False

    def __call__(self):
        return self.top, self.left, self.bottom, self.right

    def __iter__(self):
        return iter([self.top, self.left, self.bottom, self.right])

    def __hash__(self):
        return hash((self.top, self.left, self.bottom, self.right))

    def to_json(self):
        return f"[{', '.join(map(str, self()))}]"

    def contains(self, other_rect):
        """Return true if ``other_rect`` is within this rect.
        :param Rect other_rect: Another rectangle.
        :return: Whether ``other_rect`` is within this rect.
        :rtype: bool
        """

        return (other_rect.left >= self.left and other_rect.right <= self.right and
                other_rect.top >= self.top and other_rect.bottom <= self.bottom)

    def contains_point(self, point: Union[Tuple[int], 'Point']):
        """Returns True if a point lies inside self, else returns False

        :param point: Point to probe. Either an instance of Point class or a tuple (x,y) of integers.
        :type point: Union[Tuple[int], &#39;Point&#39;]
        :return: _description_
        :rtype: _type_
        """
        x, y = point
        horz_containment = self.left <= x and x <= self.right
        vert_containment = self.top <= y and y <= self.bottom

        if horz_containment and vert_containment:
            return True
        return False

    def overlaps(self, other):
        """Return true if ``other_rect`` overlaps this rect.
        :param Rect other: Another rectangle.
        :return: Whether ``other`` overlaps this rect.
        :rtype: bool
        """
        if isinstance(other, Rect):
            overlaps = (min(self.right, other.right) > max(self.left, other.left) and
                        min(self.bottom, other.bottom) > max(self.top, other.top))
        elif isinstance(other, Line):
            overlaps = any(p.row in range(self.top, self.bottom) and
                           p.col in range(self.left, self.right) for p in other.pixels)
        else:
            return NotImplemented
        return overlaps

    def center_separation(self, other: Union['Point', Tuple[int], 'Rect']) -> float:
        """ Returns the distance between the center of each graph
        :param Rect other: Another rectangle
        :return: Distance between centroids of rectangle
        :rtype: float
        """
        if hasattr(other, 'panel'):
            other = other.panel

        if isinstance(other, Rect):
            y = other.center[1]
            x = other.center[0]
        elif isinstance(other, Point):
            y = other.row
            x = other.col
        else:
            x, y = other
        height = abs(self.center[0] - x)
        length = abs(self.center[1] - y)
        return np.hypot(length, height)

    def edge_separation(self, other: Union['Point', Tuple[int], 'Rect']) -> int:
        """Cqlculates the distance between the closest edges or corners of two rectangles or a rectangle and a point.
         If the two overlap, the distance is set to 0"""
        if isinstance(other, Point):
            y, x = other
            other = Rect([y, x, y, x]) # repeat point coordinates twice
        elif isinstance(other, Sequence) and len(other) == 2:
            x, y = other
            other = Rect([y, x, y, x])
        return self._edge_separation_rect(other)

    def _edge_separation_rect(self, other_rect: 'Rect') -> int:
        """Calculates the distance between the closest edges or corners of two rectangles. If the two overlap, the
        distance is set to 0
        :param other_rect: another rectangle to which the distance is computed
        :type other_rect: Rect"""
        def dist(p1, p2):
            x1, y1 = p1
            x2, y2 = p2
            width = x2 - x1
            height = y2 - y1
            return np.hypot(width, height)
        t1, l1, b1, r1 = self
        t2, l2, b2, r2 = other_rect

        left = r2 < l1
        right = r1 < l2
        bottom = b1 < t2
        top = b2 < t1
        if top and left:
            return dist((l1, t1), (r2, b2))
        elif bottom and left:
            return dist((l1, t1), (r2, t2))
        elif bottom and right:
            return dist((r1, t1), (l2, t2))
        elif right and top:
            return dist((r1, b1), (l2, b2))
        elif left:
            return l1 - r2
        elif right:
            return l2 - r1
        elif bottom:
            return t2 - b1
        elif top:
            return t1 - b2
        else:  # rectangles intersect
            return 0.

    def find_relative_orientation(self, other_rect: 'Rect') -> Tuple[bool]:
        """Returns four booleans (top, left, bottom, right) describing relative orientation
        of self wrt to the other rect. For example [True, False, False, False] 
        means that self is above the other rect.

        :param other_rect: other rectangle object
        :type other_rect: Rect
        :return: tuple of four values specifying the relative orientation of the two rectangles
        :rtype: Tuple[bool]
        """
        t1, l1, b1, r1 = self
        t2, l2, b2, r2 = other_rect

        left = r2 < l1
        right = r1 < l2
        bottom = b1 < t2
        top = b2 < t1

        return top, left, bottom, right

    def overlaps_vertically(self, other_rect: 'Rect') -> bool:
        """
        Return True if two `Rect` objects overlap along the vertical axis (i.e. when projected onto it), False otherwise
        :param Rect other_rect: other `Rect` object for which a condition is to be tested
        :return bool: True if overlap exists, False otherwise
        """
        return min(self.bottom, other_rect.bottom) > max(self.top, other_rect.top)

    def create_crop(self, figure: 'Figure') -> 'Crop':
        """Creates crop from the rectangle in figure
        :return: crop containing the rectangle
        :rtype: Crop"""
        return Crop(figure, self)

    def create_padded_crop(self, figure: 'Figure', pad_width: int=10) -> 'Crop':
        """Creates a crop from the rectangle in figure and pads it
        :return: padded crop containing the rectangle
        :rtype: Crop"""
        crop = self.create_crop(figure)
        crop  = crop.pad_crop(pad_width)  # adjusts the img and connected_components attributes too
        return crop

    def create_extended_crop(self, figure:' Figure', extension: int) -> 'Crop':
        """Creates a crop from the rectangle and its surroundings in figure
        :return: crop containing the rectangle and its neighbourhood
        :rtype: Crop"""
        top, left, bottom, right = self
        left, right = left - extension, right + extension
        top, bottom = top - extension, bottom + extension
        return Panel((top, left, bottom, right), figure).create_crop(figure)

    def compute_iou(self, other_rect: 'Rect') -> float:
        """Computes intersection over union (IoU) value between `self` and `other rect`.

        :param other_rect: another rectangle
        :type other_rect: Rect
        :return: IoU value
        :rtype: float
        """
        xa, ya, wa, ha = self
        xb, yb, wb, hb = other_rect
        x1_inter = max(xa, xb)
        y1_inter = max(ya, yb)
        x2_inter = min(xa + wa, xb + wb)
        y2_inter = min(ya + ha, yb + hb)

        if x2_inter < x1_inter or y2_inter < y1_inter:
            return 0.0

        area_a = wa * ha
        area_b = wb * hb

        w_inter = x2_inter - x1_inter
        h_inter = y2_inter - y1_inter

        area_intersection = w_inter * h_inter
        iou = area_intersection / (area_a + area_b - area_intersection)
        return iou


class Panel(Rect, figure.GlobalFigureMixin):

    @classmethod
    def create_megapanel(cls, boxes: List['Panel'], fig: 'Figure') -> 'Panel':
        """
        Creates a large panel out of all constituent boxes (rectangles containing connected components) and associates
        it with ``fig``
        :param iterable boxes: list of bounding boxes to combine into a larger box
        :return: a large rectangle covering all smaller rectangles
        """
        top = min(rect.top for rect in boxes)
        bottom = max(rect.bottom for rect in boxes)
        left = min(rect.left for rect in boxes)
        right = max(rect.right for rect in boxes)
        tags = []
        for panel in boxes:
            if panel.tags:
                tags.extend(panel.tags)

        megabox = cls((top, left, bottom, right), fig, tags=tags)
        return megabox

    """ Tagged section inside Figure
    :param coords: (top, left, bottom, right) coordinates of the top-left and bottom-right points
    :type coords: (int, int, int, int)
    :param fig: main figure
    :type fig: Figure
    :param tags: tags of the panel (usually assigned by ndi.labels routine or similar; corresponds to pixel cluster id value)
    :type tags: int
    :param role: role assigned to the panel in a figure, selected from the Figure Role enum
    :type role: FigureRoleEnum
    :param parent_panel: a larger panel that `self` is part of (if any)
    :type parent_panel: Panel
    """
    def __init__(self, coords, fig=None, tags=None):
        Rect.__init__(self, coords)
        figure.GlobalFigureMixin.__init__(self, fig)
        self.tags = tags

        self.role = None
        self.parent_panel = None
        self._crop = None
        self._pixel_ratio = None
        self._pixels = []
        self._zipped_pixels = []

    @property
    def pixel_ratio(self):
        return self._pixel_ratio

    @pixel_ratio.setter
    def pixel_ratio(self, pixel_ratio):
        self._pixel_ratio = pixel_ratio

    @property
    def crop(self):
        if not self._crop:
            self._crop = Crop(self.fig, [self.top, self.left, self.bottom, self.right])
        return self._crop

    @property
    def pixels(self):
        """All pixels belonging to `self` in `self.fig.img`"""
        if not self._pixels:
            x, y = [], []
            for tag in self.tags:
                y_tag, x_tag = np.where(self.fig.labelled_img == tag)
                x.extend(x_tag)
                y.extend(y_tag)
            self._pixels = y, x
            self._zipped_pixels = set(zip(*self._pixels))
        return self._pixels
    
    def mask_off(self, fig: 'Figure') -> None:
        fig.img[self.pixels] = 0

    def merge_underlying_panels(self, fig: 'Figure') -> 'Panel':
        """
        Merges all underlying connected components of the panel (made up of multiple dilated,
        merged connected components) to create a single, large panel.
        All connected components in ``fig`` that are entirely within the panel are merged to create an undilated
        super-panel (important for standardisation)
        :param Figure fig: Analysed figure
        :return: Panel; super-panel made from all connected components that constitute the large panel in raw figure
        :rtype: Panel
        """
        ccs_to_merge = [cc for cc in fig.connected_components if self.contains(cc)]
        return Rect.create_megarect(ccs_to_merge)

    def in_original_fig(self, as_str: bool=True) -> Union[Tuple, str]:
        """Transforms `self.coords` to the define the same panel in the main figure. If the figure has not been rescaled,
        returns `self.coords`. Returns either a tuple of values or a string for easier export to file."""

        assert self.fig is not None, "Cannot convert coordinates to original values - this panel has not been associated" \
                                     " with any figure"
        if self.fig._scaling_factor:
            ret = list(np.rint(np.asarray(self.coords) / self.fig.scaling_factor).astype(np.int32))
        else:
            ret = self.coords
        if as_str:
            ret = list(map(str, ret))
        return ret

    def contains_any_pixel_of(self, other_panel: 'Panel') -> 'Panel':
        """Checks whether `self` contains any pixel of `other_panel`. This check is more
        sensitive than simply checking IoU between boxes.

        :param other_panel: another panel, pixels of which are checked against `self.pixels`
        :type other_panel: Panel
        :return: True if any pixels are within both `self` and `other_panel`
        :rtype: Panel
        """
        if self.pixels:
            zipped_pixels = self._zipped_pixels
        if other_panel.pixels:
            other_zipped_pixels = other_panel._zipped_pixels
        if len(zipped_pixels.intersection(other_zipped_pixels)) > 0:
            return True
        return False

class Figure(object):
    """A class describing the processed figure."""

    def __init__(self, img, raw_img, img_detectron=None, eager_cc_init=True):
        """
        :param numpy.ndarray img: Figure img.
        :param numpy.ndarray raw_img: raw img (without preprocessing, e.g. binarisation)
        :param numpy.ndarray img_detectron: image loaded using the default settings, suitable for prediction in detectron
        :param bool eager_cc_init: whether the connected components should be computed eagerly (during this initialization)
        or lazily at a later stage - for optimized performance
        :param numpy.ndarray labelled_img: an array of tags returned by the routine searching for connected components 
        """
        self._connected_components = None
        self._img = None
        self.eager_cc_init = eager_cc_init
        self._img = img
        self.raw_img = raw_img
        self.img_detectron = img_detectron
        self.labelled_img = None
        self.single_bond_length = None
        self.width, self.height = img.shape[1], img.shape[0]
        self.center = (int(self.width * 0.5), int(self.height) * 0.5)
        self._scaling_factor = None

    @property
    def img(self):
        return self._img

    @property
    def connected_components(self):
        if self._connected_components is None:
            self.set_connected_components()
        return self._connected_components

    @connected_components.setter
    def connected_components(self, value):
        self._connected_components = value

    @property
    def scaling_factor(self):
        return self._scaling_factor

    @img.setter
    def img(self, value):
        if self._img is None:
            self._img = value
        else:
            self._img = value
            self.set_connected_components()

    def __repr__(self):
        return '<%s>' % self.__class__.__name__

    def __str__(self):
        return '<%s>' % self.__class__.__name__

    def __eq__(self, other):
        return (self.img == other.img).all()

    @property
    def diagonal(self):
        return np.hypot(self.width, self.height)

    @property
    def area(self):
        panel = self.get_bounding_box()
        return panel.area


    def get_bounding_box(self):
        """ Returns the Panel object for the extreme bounding box of the img
        :rtype: Panel()"""

        return Panel((0, 0, self.img.shape[0], self.img.shape[1]))

    def set_connected_components(self):
        """
        Convenience function that tags ccs in an img and creates their Panels
        :return set: set of Panels of connected components
        """
        panels = []
        _, labelled, stats, _ = cv2.connectedComponentsWithStats(cv2.threshold
                                                          (self.img, *ProcessorConfig.BIN_THRESH, cv2.THRESH_BINARY)[1],
                                                          connectivity=8)
        self.labelled_img = labelled
        for label, cc_stat in enumerate(stats):
            x1, y1, w, h, _ = cc_stat
            if w*h < self.area * 0.95:  # Spurious cc encompassing the whole image is sometimes produced
                x2, y2 = x1 + w, y1 + h
                panels.append(Panel((y1, x1, y2, x2), fig=self, tags=[label]))

        self._connected_components = panels

    def resize(self, *args,  eager_cc_init=True, **kwargs):
        """Simple wrapper around opencv resize"""
        return Figure(cv2.resize(self.img, *args, **kwargs), raw_img=self.raw_img, eager_cc_init=eager_cc_init)

    def set_roles(self, panels, role):
        for panel in panels:
            parent_cc = [cc for cc in self.connected_components if cc.contains(panel)]
            for cc in parent_cc:
                cc.role = role


class Crop(Figure):
    """Class used to represent crops of figures with links to the main figure and crop paratemeters, as well as
    connected components both in the main coordinate system and in-crop coordinate system
    :param main_figure: the parent figure
    :type main_figure: Figure
    :param crop_params: parameters of the crop (either left, right, top, bottom tuple or Rect() with these attributes)
    :type crop_params: tuple|Rect
    :param padding: How much padding was added after the image was cropped -either a single value, a tuple of two pairs
    of values consistent with `pad_width` in numpy.pad
    :type padding: int|tuple((int, int),(int, int))
    """
    def __init__(self, main_figure: 'Figure', crop_params: Tuple[int]):
        self.main_figure = main_figure
        self.crop_params = crop_params  # (top, left, bottom, right) of the intended crop or Rect() with these attribs
        self.padding = None
        self._top_padding = 0
        self._left_padding = 0
        self.cropped_rect = None  # Actual rectangle used for the crop - different if crop_params are out of fig bounds
        self._crop_main_figure()

    def pad_crop(self, pad_width: Union[Sequence, int]) -> 'Crop':
        """Pads crop using numpy.pad, adjusts connected components appropriately
        :param pad_width: padding width which will be fed into np.pad
        :type pad_width: Union[Sequence, int]
        :return: returns mutated self, with its image padded
        :rtype: Crop
        """

        self.img = np.pad(self.img, pad_width=pad_width)
        self.padding = pad_width
        self._top_padding = pad_width if isinstance(pad_width, int) else pad_width[0][0]
        self._left_padding = pad_width if isinstance(pad_width, int) else pad_width[1][0]

        for cc in self.connected_components:
            cc.top, cc.bottom = cc.top + self._top_padding, cc.bottom + self._top_padding
            cc.left, cc.right = cc.left + self._left_padding, cc.right + self._left_padding

        return self

    def in_main_fig(self, element: Union['Panel', 'Point']) -> Union['Panel', 'Point']:
        """
        Transforms coordinates of ``cc`` (from ``self.connected_components``) to give coordinates of the
        corresponding cc in ``self.main_figure''. Returns a new  object
        :param Panel|Point element: connected component or point to transform to main coordinate system
        :return: corresponding Panel|Rect object
        :rtype: type(element)
        `"""
        if hasattr(element, '__len__') and len(element) == 2:
            y, x = element
            return y + self.cropped_rect.top - self._top_padding, x + self.cropped_rect.left - self._left_padding

        else:
            new_top = element.top + self.cropped_rect.top - self._top_padding
            new_bottom = new_top + element.height - self._top_padding
            new_left = element.left + self.cropped_rect.left - self._left_padding
            new_right = new_left + element.width - self._left_padding
            new_element = element.__class__((new_top, new_left, new_bottom, new_right), self.main_figure)
            new_element.role = element.role
            return new_element

    def in_crop(self, cc):
        """
        Transforms coordinates of ''cc'' (from ``self.main_figure.connected_components``) to give coordinates of the
        corresponding cc within a crop. Returns a new  object
        :param Panel cc: connected component to transform
        :return: Panel object with new in-crop attributes
        :rtype: type(cc)
        """
        new_top = cc.top - self.cropped_rect.top
        new_bottom = new_top + cc.height

        new_left = cc.left - self.cropped_rect.left
        new_right = new_left + cc.width
        new_obj = cc.__class__((new_top, new_left, new_bottom, new_right), fig=self.main_figure)
        new_obj.role = cc.role
        return new_obj

    def _crop_main_figure(self):
        """
        Crop img.
        Automatically limits the crop if bounds are outside the img.
        :return: Cropped img.
        :rtype: numpy.ndarray
        """
        img = self.main_figure.img
        raw_img = self.main_figure.raw_img
        img_detectron = self.main_figure.img_detectron
        if isinstance(self.crop_params, Collection):
            top, left, bottom, right = self.crop_params
        else:
            p = self.crop_params
            top, left, bottom, right = p.top, p.left, p.bottom, p.right

        height, width = img.shape[:2]

        left = max(0, left if left else 0)
        right = min(width, right if right else width)
        top = max(0, top if top else 0)
        bottom = min(height, bottom if bottom else width)
        out_img = img[top: bottom, left: right]
        out_raw_img = raw_img[top:bottom, left:right]
        if img_detectron is not None:
            d_top, d_left, d_bottom, d_right = Panel((top, left, bottom, right), fig=self.main_figure).in_original_fig(as_str=False)
            out_detectron_img = img_detectron[d_top:d_bottom, d_left:d_right]
        else:
            out_detectron_img = img_detectron

        self.cropped_rect = Rect((top, left, bottom, right))

        super().__init__(out_img, out_raw_img, img_detectron=out_detectron_img)
        if img_detectron is not None:
            self._scaling_factor = self.main_figure.scaling_factor
