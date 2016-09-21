from ...core.options import Store, Cycle, Options
from ...core import (Overlay, NdOverlay, Layout, NdLayout, GridSpace,
                     GridMatrix)
from ...interface.seaborn import *
from ...element import * 
from .renderer import PlotlyRenderer
from .element import *
from .chart import *
from .chart3d import *
from .raster import *
from .plot import *

Store.renderers['plotly'] = PlotlyRenderer.instance()

Store.register({Points: PointPlot,
                Scatter: PointPlot,
                Curve: CurvePlot,
                Raster: RasterPlot,
                Image: RasterPlot,
                Surface: SurfacePlot,
                Bivariate: BivariatePlot,
                Distribution: DistributionPlot,
                HeatMap: HeatMapPlot,
                VectorField: VectorPlot,
                ErrorBars: ErrorBarsPlot,
                Scatter3D: Scatter3dPlot,
                Trisurface: TrisurfacePlot,
                Overlay: OverlayPlot,
                NdOverlay: OverlayPlot,
                Layout: LayoutPlot,
                NdLayout: LayoutPlot}, backend='plotly')
