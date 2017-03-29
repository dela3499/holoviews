import warnings
from operator import itemgetter
from itertools import product
import numpy as np
import colorsys
import param

from ..core import util
from ..core.data import (ArrayInterface, NdElementInterface, DictInterface,
                         ImageInterface)
from ..core import (Dimension, NdMapping, Element2D, HoloMap,
                    Overlay, Element, Dataset, NdElement)
from ..core.boundingregion import BoundingRegion, BoundingBox
from ..core.sheetcoords import SheetCoordinateSystem, Slice
from ..core.util import pd
from .chart import Curve
from .tabular import Table
from .util import compute_edges, categorical_aggregate2d

try:
    from ..core.data import PandasInterface
except ImportError:
    PandasInterface = None


class Raster(Element2D):
    """
    Raster is a basic 2D element type for presenting either numpy or
    dask arrays as two dimensional raster images.

    Arrays with a shape of (N,M) are valid inputs for Raster wheras
    subclasses of Raster (e.g. RGB) may also accept 3D arrays
    containing channel information.

    Raster does not support slicing like the Image or RGB subclasses
    and the extents are in matrix coordinates if not explicitly
    specified.
    """

    kdims = param.List(default=[Dimension('x'), Dimension('y')],
                       bounds=(2, 2), constant=True, doc="""
        The label of the x- and y-dimension of the Raster in form
        of a string or dimension object.""")

    group = param.String(default='Raster', constant=True)

    vdims = param.List(default=[Dimension('z')],
                       bounds=(1, 1), doc="""
        The dimension description of the data held in the matrix.""")

    def __init__(self, data, extents=None, **params):
        if extents is None:
            (d1, d2) = data.shape[:2]
            extents = (0, 0, d2, d1)
        super(Raster, self).__init__(data, extents=extents, **params)


    def __getitem__(self, slices):
        if slices in self.dimensions(): return self.dimension_values(slices)
        slices = util.process_ellipses(self,slices)
        if not isinstance(slices, tuple):
            slices = (slices, slice(None))
        elif len(slices) > (2 + self.depth):
            raise KeyError("Can only slice %d dimensions" % 2 + self.depth)
        elif len(slices) == 3 and slices[-1] not in [self.vdims[0].name, slice(None)]:
            raise KeyError("%r is the only selectable value dimension" % self.vdims[0].name)

        slc_types = [isinstance(sl, slice) for sl in slices[:2]]
        data = self.data.__getitem__(slices[:2][::-1])
        if all(slc_types):
            return self.clone(data, extents=None)
        elif not any(slc_types):
            return data
        else:
            return self.clone(np.expand_dims(data, axis=slc_types.index(True)),
                              extents=None)


    def dimension_values(self, dim, expanded=True, flat=True):
        """
        The set of samples available along a particular dimension.
        """
        dim_idx = self.get_dimension_index(dim)
        if not expanded and dim_idx == 0:
            return np.array(range(self.data.shape[1]))
        elif not expanded and dim_idx == 1:
            return np.array(range(self.data.shape[0]))
        elif dim_idx in [0, 1]:
            values = np.mgrid[0:self.data.shape[1], 0:self.data.shape[0]][dim_idx]
            return values.flatten() if flat else values
        elif dim_idx == 2:
            arr = self.data.T
            return arr.flatten() if flat else arr
        else:
            return super(Raster, self).dimension_values(dim)


    @classmethod
    def collapse_data(cls, data_list, function, kdims=None, **kwargs):
        if isinstance(function, np.ufunc):
            return function.reduce(data_list)
        else:
            return function(np.dstack(data_list), axis=-1, **kwargs)


    def sample(self, samples=[], **sample_values):
        """
        Sample the Raster along one or both of its dimensions,
        returning a reduced dimensionality type, which is either
        a ItemTable, Curve or Scatter. If two dimension samples
        and a new_xaxis is provided the sample will be the value
        of the sampled unit indexed by the value in the new_xaxis
        tuple.
        """
        if isinstance(samples, tuple):
            X, Y = samples
            samples = zip(X, Y)

        params = dict(self.get_param_values(onlychanged=True),
                      vdims=self.vdims)
        params.pop('extents', None)
        params.pop('bounds', None)
        if len(sample_values) == self.ndims or len(samples):
            if not len(samples):
                samples = zip(*[c if isinstance(c, list) else [c] for _, c in
                               sorted([(self.get_dimension_index(k), v) for k, v in
                                       sample_values.items()])])
            table_data = [c+(self._zdata[self._coord2matrix(c)],)
                          for c in samples]
            params['kdims'] = self.kdims
            return Table(table_data, **params)
        else:
            dimension, sample_coord = list(sample_values.items())[0]
            if isinstance(sample_coord, slice):
                raise ValueError(
                    'Raster sampling requires coordinates not slices,'
                    'use regular slicing syntax.')
            # Indices inverted for indexing
            sample_ind = self.get_dimension_index(dimension)
            if sample_ind is None:
                raise Exception("Dimension %s not found during sampling" % dimension)
            other_dimension = [d for i, d in enumerate(self.kdims) if
                               i != sample_ind]

            # Generate sample slice
            sample = [slice(None) for i in range(self.ndims)]
            coord_fn = (lambda v: (v, 0)) if not sample_ind else (lambda v: (0, v))
            sample[sample_ind] = self._coord2matrix(coord_fn(sample_coord))[abs(sample_ind-1)]

            # Sample data
            x_vals = self.dimension_values(other_dimension[0].name, False)
            ydata = self._zdata[sample[::-1]]
            if hasattr(self, 'bounds') and sample_ind == 0: ydata = ydata[::-1]
            data = list(zip(x_vals, ydata))
            params['kdims'] = other_dimension
            return Curve(data, **params)


    def reduce(self, dimensions=None, function=None, **reduce_map):
        """
        Reduces the Raster using functions provided via the
        kwargs, where the keyword is the dimension to be reduced.
        Optionally a label_prefix can be provided to prepend to
        the result Element label.
        """
        function, dims = self._reduce_map(dimensions, function, reduce_map)
        if len(dims) == self.ndims:
            if isinstance(function, np.ufunc):
                return function.reduce(self.data, axis=None)
            else:
                return function(self.data)
        else:
            dimension = dims[0]
            other_dimension = [d for d in self.kdims if d.name != dimension]
            oidx = self.get_dimension_index(other_dimension[0])
            x_vals = self.dimension_values(other_dimension[0].name, False)
            reduced = function(self._zdata, axis=oidx)
            if oidx and hasattr(self, 'bounds'):
                reduced = reduced[::-1]
            data = zip(x_vals, reduced)
            params = dict(dict(self.get_param_values(onlychanged=True)),
                          kdims=other_dimension, vdims=self.vdims)
            params.pop('bounds', None)
            params.pop('extents', None)
            return Table(data, **params)


    @property
    def depth(self):
        return len(self.vdims)


    @property
    def _zdata(self):
        return self.data


    def _coord2matrix(self, coord):
        return int(round(coord[1])), int(round(coord[0]))




class Image(Dataset, Element2D, SheetCoordinateSystem):
    """
    Image is the atomic unit as which 2D data is stored, along with
    its bounds object. The input data may be a numpy.matrix object or
    a two-dimensional numpy array.

    Allows slicing operations of the data in sheet coordinates or direct
    access to the data, via the .data attribute.
    """

    bounds = param.ClassSelector(class_=BoundingRegion, default=None, doc="""
       The bounding region in sheet coordinates containing the data.""")

    datatype = param.List(default=['image', 'grid', 'xarray', 'dataframe', 'dictionary'])

    group = param.String(default='Image', constant=True)

    kdims = param.List(default=[Dimension('x'), Dimension('y')],
                       bounds=(2, 2), constant=True, doc="""
        The label of the x- and y-dimension of the Raster in form
        of a string or dimension object.""")

    vdims = param.List(default=[Dimension('z')],
                       bounds=(1, None), doc="""
        The dimension description of the data held in the matrix.""")

    def __init__(self, data, bounds=None, extents=None, xdensity=None, ydensity=None, **params):
        extents = extents if extents else (None, None, None, None)
        if data is None: data = np.array([[0]])
        Dataset.__init__(self, data, extents=extents, bounds=None, **params)

        if isinstance(data, np.ndarray):
            dim2, dim1 = data.shape[:2]
        else:
            dim1, dim2 = tuple([len(self.dimension_values(kd, expanded=False))
                                  for kd in self.kdims])
        if self.bounds is not None:
            bounds = self.bounds
        elif bounds is None and isinstance(data, np.ndarray):
            bounds = BoundingBox()

        if bounds is None:
            xvals = self.dimension_values(0, False)
            l, r, xdensity, _ = util.bound_range(xvals, xdensity)
            yvals = self.dimension_values(1, False)
            b, t, ydensity, _ = util.bound_range(yvals, ydensity)
            bounds = BoundingBox(points=((l, b), (r, t)))
        elif np.isscalar(bounds):
            bounds = BoundingBox(radius=bounds)
        elif isinstance(bounds, (tuple, list, np.ndarray)):
            l, b, r, t = bounds
            bounds = BoundingBox(points=((l, b), (r, t)))

        l, b, r, t = bounds.lbrt()
        xdensity = xdensity if xdensity else dim1/float(r-l)
        ydensity = ydensity if ydensity else dim2/float(t-b)
        SheetCoordinateSystem.__init__(self, bounds, xdensity, ydensity)

        if len(self.shape) == 3:
            if self.shape[2] != len(self.vdims):
                raise ValueError("Input array has shape %r but %d value dimensions defined"
                                 % (self.shape, len(self.vdims)))


    def __setstate__(self, state):
        """
        Ensures old-style unpickled Image types without an interface
        use the ImageInterface.
        """
        self.__dict__ = state
        if isinstance(self.data, np.ndarray):
            self.interface = ImageInterface
        super(Dataset, self).__setstate__(state)


    def aggregate(self, dimensions=None, function=None, spreadfn=None, **kwargs):
        agg = super(Image, self).aggregate(dimensions, function, spreadfn, **kwargs)
        return Curve(agg) if isinstance(agg, Dataset) else agg


    def select(self, selection_specs=None, **selection):
        """
        Allows selecting data by the slices, sets and scalar values
        along a particular dimension. The indices should be supplied as
        keywords mapping between the selected dimension and
        value. Additionally selection_specs (taking the form of a list
        of type.group.label strings, types or functions) may be
        supplied, which will ensure the selection is only applied if the
        specs match the selected object.
        """
        if selection_specs and not any(self.matches(sp) for sp in selection_specs):
            return self

        selection = {self.get_dimension(k).name: slice(*sel) if isinstance(sel, tuple) else sel
                     for k, sel in selection.items() if k in self.kdims}
        coords = tuple(selection[kd.name] if kd.name in selection else slice(None)
                       for kd in self.kdims)

        indexed = False
        if not any([isinstance(el, slice) for el in coords]):
            indexed = True
            selection = {kd.name: c for kd, c in zip(self.kdims, self.closest(coords))}

        # Compute new bounds
        shape = self.interface.shape(self)
        ys, xs = shape[:2]
        xidx, yidx = coords
        xdim, ydim = self.kdims
        l, b, r, t = self.bounds.lbrt()
        xdensity, ydensity = self.xdensity, self.ydensity
        xunit = (1./xdensity)
        yunit = (1./ydensity)
        if isinstance(xidx, slice):
            l = l if xidx.start is None else max(l, xidx.start)
            r = r if xidx.stop is None else min(r, xidx.stop)
        if isinstance(yidx, slice):
            b = b if yidx.start is None else max(b, yidx.start)
            t = t if yidx.stop is None else min(t, yidx.stop)
        bounds = BoundingBox(points=((l, b), (r, t)))

        # Apply new bounds
        slc = Slice(bounds, self)

        # Apply scalar and list indices
        l, b, r, t = slc.compute_bounds(self).lbrt()
        if indexed:
            pass
        elif not isinstance(xidx, slice):
            if not isinstance(xidx, (list, set)): xidx = [xidx]
            if len(xidx) > 1:
                xdensity = xdensity*(float(len(xidx))/xs)
            idxs = []
            ls, rs = [], []
            for idx in xidx:
                xc, _ = self.closest_cell_center(idx, b)
                ls.append(xc-xunit/2)
                rs.append(xc+xunit/2)
            l, r = np.min(ls), np.max(rs)
            selection[xdim.name] = slice(l, r)
        elif not isinstance(yidx, slice):
            if not isinstance(yidx, (set, list)): yidx = [yidx]
            if len(yidx) > 1:
                ydensity = ydensity*(float(len(yidx))/ys)
            idxs = []
            bs, ts = [], []
            for idx in yidx:
                _, yc = self.closest_cell_center(l, idx)
                bs.append(yc-yunit/2)
                ts.append(yc+yunit/2)
            b, t = np.min(bs), np.max(ts)
            selection[ydim.name] = slice(b, t)

        bounds = BoundingBox(points=((l, b), (r, t)))
        data = self.interface.select(self, **selection)
        if isinstance(data, np.ndarray) and data.ndim == 1:
            return self.clone([tuple(data)], kdims=[], new_type=Dataset)
        elif np.isscalar(data):
            return data
        else:
            return self.clone(data, xdensity=self.xdensity,
                              ydensity=self.ydensity, bounds=bounds)


    def closest(self, coords=[], **kwargs):
        """
        Given a single coordinate or multiple coordinates as
        a tuple or list of tuples or keyword arguments matching
        the dimension closest will find the closest actual x/y
        coordinates.
        """
        if kwargs and coords:
            raise ValueError("Specify coordinate using as either a list "
                             "keyword arguments not both")
        if kwargs:
            coords = []
            getter = []
            for k, v in kwargs.items():
                idx = self.get_dimension_index(k)
                if np.isscalar(v):
                    coords.append((0, v) if idx else (v, 0))
                else:
                    if isinstance(v, list):
                        coords = [(0, c) if idx else (c, 0) for c in v]
                    if len(coords) not in [0, len(v)]:
                        raise ValueError("Length of samples must match")
                    elif len(coords):
                        coords = [(t[abs(idx-1)], c) if idx else (c, t[abs(idx-1)])
                                  for c, t in zip(v, coords)]
                getter.append(idx)
        else:
            getter = [0, 1]
        getter = itemgetter(*sorted(getter))
        if len(coords) == 1:
            coords = coords[0]
        if isinstance(coords, tuple):
            return getter(self.closest_cell_center(*coords))
        else:
            return [getter(self.closest_cell_center(*el)) for el in coords]


    def range(self, dim, data_range=True):
        idx = self.get_dimension_index(dim)
        dimension = self.get_dimension(dim)
        rng = super(Image, self).range(dim, data_range)
        if idx in [0, 1] and data_range and dimension.range == (None, None):
            low, high = rng
            density = self.ydensity if idx else self.xdensity
            halfd = (1./density)/2.
            return (low-halfd, high+halfd)
        return rng


    def table(self, datatype=None):
        """
        Converts the data Element to a Table, optionally may
        specify a supported data type. The default data types
        are 'numpy' (for homogeneous data), 'dataframe', and
        'dictionary'.
        """
        if datatype and not isinstance(datatype, list):
            datatype = [datatype]
        from ..element import Table
        return self.clone(self.columns(), new_type=Table,
                          **(dict(datatype=datatype) if datatype else {}))


    def _coord2matrix(self, coord):
        return self.sheet2matrixidx(*coord)


GridImage = Image


class RGB(Image):
    """
    An RGB element is a Image containing channel data for the the
    red, green, blue and (optionally) the alpha channels. The values
    of each channel must be in the range 0.0 to 1.0.

    In input array may have a shape of NxMx4 or NxMx3. In the latter
    case, the defined alpha dimension parameter is appended to the
    list of value dimensions.
    """

    group = param.String(default='RGB', constant=True)

    alpha_dimension = param.ClassSelector(default=Dimension('A',range=(0,1)),
                                          class_=Dimension, instantiate=False,  doc="""
        The alpha dimension definition to add the value dimensions if
        an alpha channel is supplied.""")

    vdims = param.List(
        default=[Dimension('R', range=(0,1)), Dimension('G',range=(0,1)),
                 Dimension('B', range=(0,1))], bounds=(3, 4), doc="""
        The dimension description of the data held in the matrix.

        If an alpha channel is supplied, the defined alpha_dimension
        is automatically appended to this list.""")

    _vdim_reductions = {1: Image}

    @property
    def rgb(self):
        """
        Returns the corresponding RGB element.

        Other than the updating parameter definitions, this is the
        only change needed to implemented an arbitrary colorspace as a
        subclass of RGB.
        """
        return self


    @classmethod
    def load_image(cls, filename, height=1, array=False, bounds=None, bare=False, **kwargs):
        """
        Returns an raster element or raw numpy array from a PNG image
        file, using matplotlib.

        The specified height determines the bounds of the raster
        object in sheet coordinates: by default the height is 1 unit
        with the width scaled appropriately by the image aspect ratio.

        Note that as PNG images are encoded as RGBA, the red component
        maps to the first channel, the green component maps to the
        second component etc. For RGB elements, this mapping is
        trivial but may be important for subclasses e.g. for HSV
        elements.

        Setting bare=True will apply options disabling axis labels
        displaying just the bare image. Any additional keyword
        arguments will be passed to the Image object.
        """
        try:
            from matplotlib import pyplot as plt
        except:
            raise ImportError("RGB.load_image requires matplotlib.")

        data = plt.imread(filename)
        if array:  return data

        (h, w, _) = data.shape
        if bounds is None:
            f = float(height) / h
            xoffset, yoffset = w*f/2, h*f/2
            bounds=(-xoffset, -yoffset, xoffset, yoffset)
        rgb = cls(data, bounds=bounds, **kwargs)
        if bare: rgb = rgb(plot=dict(xaxis=None, yaxis=None))
        return rgb


    def __init__(self, data, **params):
        sliced = None
        if isinstance(data, Overlay):
            images = data.values()
            if not all(isinstance(im, Image) for im in images):
                raise ValueError("Input overlay must only contain Image elements")
            shapes = [im.data.shape for im in images]
            if not all(shape==shapes[0] for shape in shapes):
                raise ValueError("Images in the input overlays must contain data of the consistent shape")
            ranges = [im.vdims[0].range for im in images]
            if any(None in r for r in ranges):
                raise ValueError("Ranges must be defined on all the value dimensions of all the Images")
            arrays = [(im.data - r[0]) / (r[1] - r[0]) for r,im in zip(ranges, images)]
            data = np.dstack(arrays)
        vdims = list(params.get('vdims', self.vdims))
        if isinstance(data, np.ndarray):
            if data.shape[-1] == 4 and len(vdims) == 3:
                vdims.append(self.alpha_dimension)
                params['vdims'] = vdims
        super(RGB, self).__init__(data, **params)



class HSV(RGB):
    """
    Example of a commonly used color space subclassed from RGB used
    for working in a HSV (hue, saturation and value) color space.
    """

    group = param.String(default='HSV', constant=True)

    alpha_dimension = param.ClassSelector(default=Dimension('A',range=(0,1)),
                                          class_=Dimension, instantiate=False,  doc="""
        The alpha dimension definition to add the value dimensions if
        an alpha channel is supplied.""")

    vdims = param.List(
        default=[Dimension('H', range=(0,1), cyclic=True),
                 Dimension('S',range=(0,1)),
                 Dimension('V', range=(0,1))], bounds=(3, 4), doc="""
        The dimension description of the data held in the array.

        If an alpha channel is supplied, the defined alpha_dimension
        is automatically appended to this list.""")

    hsv_to_rgb = np.vectorize(colorsys.hsv_to_rgb)

    @property
    def rgb(self):
        """
        Conversion from HSV to RGB.
        """
        data = [self.dimension_values(d, flat=False)
                for d in self.vdims]

        hsv = self.hsv_to_rgb(*data[:3])
        if len(self.vdims) == 4:
            hsv += (data[3],)

        return RGB(np.dstack(hsv), bounds=self.bounds,
                   group=self.group, label=self.label)


class QuadMesh(Raster):
    """
    QuadMesh is a Raster type to hold x- and y- bin values
    with associated values. The x- and y-values of the QuadMesh
    may be supplied either as the edges of each bin allowing
    uneven sampling or as the bin centers, which will be converted
    to evenly sampled edges.

    As a secondary but less supported mode QuadMesh can contain
    a mesh of quadrilateral coordinates that is not laid out in
    a grid. The data should then be supplied as three separate
    2D arrays for the x-/y-coordinates and grid values.
    """

    group = param.String(default="QuadMesh", constant=True)

    kdims = param.List(default=[Dimension('x'), Dimension('y')])

    vdims = param.List(default=[Dimension('z')], bounds=(1,1))

    def __init__(self, data, **params):
        data = self._process_data(data)
        Element2D.__init__(self, data, **params)
        self.data = self._validate_data(self.data)
        self._grid = self.data[0].ndim == 1


    @property
    def depth(self): return 1


    def _process_data(self, data):
        data = tuple(np.array(el) for el in data)
        x, y, zarray = data
        ys, xs = zarray.shape
        if x.ndim == 1 and len(x) == xs:
            x = compute_edges(x)
        if y.ndim == 1 and len(y) == ys:
            y = compute_edges(y)
        return (x, y, zarray)


    @property
    def _zdata(self):
        return self.data[2]


    def _validate_data(self, data):
        x, y, z = data
        if not z.ndim == 2:
            raise ValueError("Z-values must be 2D array")

        ys, xs = z.shape
        shape_errors = []
        if x.ndim == 1 and xs+1 != len(x):
            shape_errors.append('x')
        if x.ndim == 1 and ys+1 != len(y):
            shape_errors.append('y')
        if shape_errors:
            raise ValueError("%s-edges must match shape of z-array." %
                             '/'.join(shape_errors))
        return data


    def __getitem__(self, slices):
        if slices in self.dimensions(): return self.dimension_values(slices)
        slices = util.process_ellipses(self,slices)
        if not self._grid:
            raise KeyError("Indexing of non-grid based QuadMesh"
                             "currently not supported")
        if len(slices) > (2 + self.depth):
            raise KeyError("Can only slice %d dimensions" % (2 + self.depth))
        elif len(slices) == 3 and slices[-1] not in [self.vdims[0].name, slice(None)]:
            raise KeyError("%r is the only selectable value dimension" % self.vdims[0].name)
        slices = slices[:2]
        if not isinstance(slices, tuple): slices = (slices, slice(None))
        slc_types = [isinstance(sl, slice) for sl in slices]
        if not any(slc_types):
            indices = []
            for idx, data in zip(slices, self.data[:self.ndims]):
                indices.append(np.digitize([idx], data)-1)
            return self.data[2][tuple(indices[::-1])]
        else:
            sliced_data, indices = [], []
            for slc, data in zip(slices, self.data[:self.ndims]):
                if isinstance(slc, slice):
                    low, high = slc.start, slc.stop
                    lidx = ([None] if low is None else
                            max((np.digitize([low], data)-1, 0)))[0]
                    hidx = ([None] if high is None else
                            np.digitize([high], data))[0]
                    sliced_data.append(data[lidx:hidx])
                    indices.append(slice(lidx, (hidx if hidx is None else hidx-1)))
                else:
                    index = (np.digitize([slc], data)-1)[0]
                    sliced_data.append(data[index:index+2])
                    indices.append(index)
            z = np.atleast_2d(self.data[2][tuple(indices[::-1])])
            if not all(slc_types) and not slc_types[0]:
                z = z.T
            return self.clone(tuple(sliced_data+[z]))


    @classmethod
    def collapse_data(cls, data_list, function, kdims=None, **kwargs):
        """
        Allows collapsing the data of a number of QuadMesh
        Elements with a function.
        """
        if not all(data[0].ndim == 1 for data in data_list):
            raise Exception("Collapsing of non-grid based QuadMesh"
                            "currently not supported")
        xs, ys, zs = zip(data_list)
        if isinstance(function, np.ufunc):
            z = function.reduce(zs)
        else:
            z = function(np.dstack(zs), axis=-1, **kwargs)
        return xs[0], ys[0], z


    def _coord2matrix(self, coord):
        return tuple((np.digitize([coord[i]], self.data[i])-1)[0]
                     for i in [1, 0])


    def range(self, dimension):
        idx = self.get_dimension_index(dimension)
        if idx in [0, 1]:
            data = self.data[idx]
            return np.min(data), np.max(data)
        elif idx == 2:
            data = self.data[idx]
            return np.nanmin(data), np.nanmax(data)
        super(QuadMesh, self).range(dimension)


    def dimension_values(self, dimension, expanded=True, flat=True):
        idx = self.get_dimension_index(dimension)
        data = self.data[idx]
        if idx in [0, 1]:
            if not self._grid:
                return data.flatten()
            odim = self.data[2].shape[idx] if expanded else 1
            vals = np.tile(np.convolve(data, np.ones((2,))/2, mode='valid'), odim)
            if idx:
                return np.sort(vals)
            else:
                return vals
        elif idx == 2:
            return data.flatten() if flat else data
        else:
            return super(QuadMesh, self).dimension_values(idx)



class HeatMap(Dataset, Element2D):
    """
    HeatMap is an atomic Element used to visualize two dimensional
    parameter spaces. It supports sparse or non-linear spaces, dynamically
    upsampling them to a dense representation, which can be visualized.

    A HeatMap can be initialized with any dict or NdMapping type with
    two-dimensional keys.
    """

    group = param.String(default='HeatMap', constant=True)

    kdims = param.List(default=[Dimension('x'), Dimension('y')])

    vdims = param.List(default=[Dimension('z')])

    def __init__(self, data, **params):
        super(HeatMap, self).__init__(data, **params)
        self.gridded = categorical_aggregate2d(self)

    @property
    def raster(self):
        self.warning("The .raster attribute on HeatMap is deprecated, "
                     "the 2D aggregate is now computed dynamically "
                     "during plotting.")
        return self.gridded.dimension_values(2, flat=False)

