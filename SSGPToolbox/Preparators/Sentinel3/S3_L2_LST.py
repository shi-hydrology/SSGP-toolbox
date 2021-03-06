'''

class S3_LST_transformer - A class for converting files from archives with LST data to the geotiff or npy format
Mission: Sentinel-3
Satellite Platform: S3A_*, S3B_*
Product Type: SL_2_LST___
Timeliness: "Near Real Time", "Short Time Critical", "Non Time Critical"

Private methods:
get_utm_code_from_extent  --- method for selecting the appropriate metric projection for the territory
preparation               --- method that extracts matrices from the archive, binds them, and prepares options for cropping the raster

Public methods:
archive_to_geotiff        --- saves the matrix in the geotiff format
archive_to_npy            --- saves the matrix in the npy format

'''

import os
import zipfile
import shutil

import numpy as np
import gdal, osr
import json
from netCDF4 import Dataset
from pyproj import Proj, transform

class S3_L2_LST():

    # file_path  --- the path to the archive
    # extent     --- dictionary {'minX': ..., 'minY': ...,'maxX': ..., 'maxY': ...}, where are the coordinates in WGS
    # resolution --- dictionary {'xRes': 1000, 'yRes': 1000}, spatial resolution, units of measurement, x-resolution, Y-resolution
    # key_values --- dictionary {'gap': -100.0, 'skip': -200.0,'noData': -32768.0}, indicates missing pixels
    # When initializing a dictionary is generated with the metadata of the self.metadata
    def __init__(self, file_path, extent, resolution, key_values = {'gap': -100.0, 'skip': -200.0,'NoData': -32768.0}):
        self.file_path = file_path
        self.extent = extent
        self.resolution = resolution
        self.key_values = key_values

        # We are dealing with directories, what and where we will place them
        main_path = os.path.split(self.file_path)[0]
        self.temporary_path = os.path.join(main_path, 'temporary') # Temporary directory where all files will be stored

        # Selecting the most appropriate metric projection
        self.utm_code, self.utm_extent = self.__get_utm_code_from_extent()
        # We write information about the satellite and the date of shooting to variables
        archive_name = os.path.basename(self.file_path)
        self.datetime = archive_name[16:31]
        self.satellite = archive_name[0:3]

        # Creating a dictionary with metadata
        self.metadata = {}
        self.metadata.update({'file_name': archive_name,
                              'satellite': self.satellite,
                              'datetime': self.datetime,
                              'extent': self.extent,
                              'utm_code': self.utm_code,
                              'utm_extent': self.utm_extent,
                              'resolution': self.resolution,})

    # The private method for the selection of the most appropriate metric projection is called when initializing the class
    # return utm_code   --- UTM projection code
    # return utm_extent --- dictionary {'minX': ..., 'minY': ...,'maxX': ..., 'maxY': ...}, where are the coordinates in UTM
    def __get_utm_code_from_extent(self):
        minX = self.extent.get('minX')
        minY = self.extent.get('minY')
        maxX = self.extent.get('maxX')
        maxY = self.extent.get('maxY')

        y_centroid = (minY + maxY) / 2
        # 326NN or 327NN - where NN is the zone number
        if y_centroid < 0:
            base_code = 32700
        else:
            base_code = 32600

        x_centroid = (minX + maxX) / 2
        zone = int(((x_centroid + 180) / 6.0) % 60) + 1
        utm_code = base_code + zone

        wgs = Proj(init="epsg:4326")
        utm = Proj(init="epsg:" + str(utm_code))
        min_corner = transform(wgs, utm, *[minX, minY])
        max_corner = transform(wgs, utm, *[maxX, maxY])
        utm_extent = {'minX': min_corner[0], 'minY': min_corner[1],'maxX': max_corner[0], 'maxY': max_corner[1]}
        return(utm_code, utm_extent)

    # Private method for generating the necessary files for spatial binding of NetCDF matrices
    # return warpOptions  --- list of options for creating a linked raster
    # return imageVRTPath --- path to the generated raster
    def __preparation(self):
        # Defining a temporary directory - if it doesn't exist (which is most likely the case), then create it
        if os.path.isdir(self.temporary_path) == False:
            os.mkdir(self.temporary_path)

        archive = zipfile.ZipFile(self.file_path, 'r')  # Opening the archive
        arch_files = archive.namelist()  # What are the folders/files in the archive

        # Accessing the NetCDF files in the archive, first extracting the files in temporary_path from the archives
        for file in arch_files:
            if file.endswith("geodetic_in.nc"):
                geodetic_in_nc = file
                geodetic_in = archive.extract(geodetic_in_nc, path = self.temporary_path)
            elif file.endswith("LST_in.nc"):
                LST_in_nc = file
                LST_in = archive.extract(LST_in_nc, path = self.temporary_path)
            elif file.endswith("flags_in.nc"):
                flags_in_nc = file
                flags_in = archive.extract(flags_in_nc, path = self.temporary_path)
            elif file.endswith("LST_ancillary_ds.nc"):
                LST_ancillary_ds_nc = file
                LST_ancillary_ds = archive.extract(LST_ancillary_ds_nc, path = self.temporary_path)

        flags_in = Dataset(flags_in)
        confidence_in = np.array(flags_in.variables['confidence_in'])  # Matrices with flags
        bayes_in = np.array(flags_in.variables['bayes_in'])

        # We need to find such values in the confidence_in matrix, in which the flag value could be included
        # as a summand
        bits_map = ['0'] * 16384
        bits_map.append('A')
        bits_map = np.array(bits_map)
        # The mask of clouds in confidence_in
        clouds_сonf_in = bits_map[confidence_in & 16384]

        bits_map = np.array(['O', 'O', 'A'])
        # The mask of clouds in bayes_in
        clouds_bayes_in = bits_map[bayes_in & 2]

        geodetic_in = Dataset(geodetic_in)
        el = np.array(geodetic_in.variables['elevation_in'])
        lat = np.array(geodetic_in.variables['latitude_in'])
        long = np.array(geodetic_in.variables['longitude_in'])

        LST_in = Dataset(LST_in)
        LST_matrix = np.array(LST_in.variables['LST'])

        LST_ancillary_ds = Dataset(LST_ancillary_ds)
        biome = np.array(LST_ancillary_ds.variables['biome'])

        # ATTENTION! The order in which flags are assigned is important, first to clouds , then to everything else
        # Otherwise, we will fill the pixel from the clouds, where the value is-inf because it is the sea
        # We mark all pixels with clouds on our matrix with the values - "gap"
        LST_matrix[clouds_сonf_in == 'A'] = self.key_values.get('gap')
        LST_matrix[clouds_bayes_in == 'A'] = self.key_values.get('gap')
        # We mark all pixels occupied by sea water in our matrix with the values - " skip"
        LST_matrix[biome == 0] = self.key_values.get('skip')

        # If we need to get the biome matrix
        if self.biomes_instead_lst == True:
            div = np.ma.array(biome)
        # Otherwise, the LST matrix is processed
        else:
            div = np.ma.array(LST_matrix)
        div = np.flip(div, axis = 0)
        lats = np.flip(lat, axis = 0)
        lons = np.flip(long, axis = 0)

        # A list of lines that are greater than so many degrees and less than so many degrees in latitude, we take with a margin
        Higher_border = self.extent.get('maxY') + 10
        Lower_border = self.extent.get('minY') - 10

        wrong_raws_1 = np.unique(np.argwhere(lats > Higher_border)[:, 0])
        wrong_raws_2 = np.unique(np.argwhere(lats < Lower_border)[:, 0])
        # Combining lists of row indexes that need to be removed
        wrong_raws = np.hstack((wrong_raws_1, wrong_raws_2))

        div = np.delete(div, (wrong_raws), axis = 0)
        lats = np.delete(lats, (wrong_raws), axis = 0)
        lons = np.delete(lons, (wrong_raws), axis = 0)

        # Set the settings for the data type and the type of driver used, as well as all paths:
        dataType = gdal.GDT_Float64
        driver = gdal.GetDriverByName("GTiff")
        latPath = os.path.join(self.temporary_path, 'lat.tif')
        lonPath = os.path.join(self.temporary_path, 'lon.tif')
        imagePath = os.path.join(self.temporary_path, 'image.tif')
        imageVRTPath = os.path.join(self.temporary_path, 'image.vrt')

        # Creating a raster for latitudes (..\TEMP\lat. tif):
        dataset = driver.Create(latPath, div.shape[1], div.shape[0], 1, dataType)
        dataset.GetRasterBand(1).WriteArray(lats)

        # Creating a raster for longitudes (..\TEMP\lon. tif)
        dataset = driver.Create(lonPath, div.shape[1], div.shape[0], 1, dataType)
        dataset.GetRasterBand(1).WriteArray(lons)

        # Creating a raster for data (..\TEMP\image.tif)
        dataset = driver.Create(imagePath, div.shape[1], div.shape[0], 1, dataType)
        dataset.GetRasterBand(1).WriteArray(div)

        # Install the WGS84 CS
        gcp_srs = osr.SpatialReference()
        gcp_srs.ImportFromEPSG(4326)
        proj4 = gcp_srs.ExportToProj4()

        # Based on the tif, we will create a vrt (..\TEMP\image. vrt)
        vrt = gdal.BuildVRT(imageVRTPath, dataset, separate = True, resampleAlg = 'cubic', outputSRS = proj4)
        band = vrt.GetRasterBand(1)

        # Bind the coordinates to the virtual raster...
        metadataGeoloc = {
            'X_DATASET': lonPath,
            'X_BAND': '1',
            'Y_DATASET': latPath,
            'Y_BAND': '1',
            'PIXEL_OFFSET': '0',
            'LINE_OFFSET': '0',
            'PIXEL_STEP': '1',
            'LINE_STEP': '1'
        }

        # ...by writing it all in <Metadata domain= 'Geolocation'>:
        vrt.SetMetadata(metadataGeoloc, "GEOLOCATION")

        dataset = None
        vrt = None

        output_rs = osr.SpatialReference()
        output_rs.ImportFromEPSG(self.utm_code)

        warpOptions = gdal.WarpOptions(geoloc = True, format = 'GTiff', dstNodata = self.key_values.get('NoData'), srcSRS = proj4, dstSRS = output_rs,
                                       outputBounds = [self.utm_extent.get('minX'), self.utm_extent.get('minY'), self.utm_extent.get('maxX'), self.utm_extent.get('maxY')],
                                       xRes = self.resolution.get('xRes'), yRes = self.resolution.get('yRes'), creationOptions = ['COMPRESS=LZW'])

        # Closing unzipped NetCDF files
        archive.close()
        geodetic_in.close()
        LST_in.close()
        flags_in.close()
        LST_ancillary_ds.close()
        return(warpOptions, imageVRTPath)
    pass

    # Method for generating the file .geotiff in the appropriate directory
    # save_path --- the location to which you want to place a file with the result
    # biomes_instead_lst --- do you need to get a landscape type matrix instead of an LST matrix
    def archive_to_geotiff(self, save_path, biomes_instead_lst = False):
        self.biomes_instead_lst = biomes_instead_lst

        if os.path.isdir(save_path) == False:
            os.mkdir(save_path)
        warpOptions, imageVRTPath = self.__preparation()

        if self.biomes_instead_lst == True:
            geotiff_name = self.datetime + '_biomes.tif'
        else:
            geotiff_name = self.datetime + '.tif'
        geotiff_path = os.path.join(save_path, geotiff_name)
        raster = gdal.Warp(geotiff_path, imageVRTPath, dstNodata = self.key_values.get('NoData'), options = warpOptions)

        # Deleting the temporary directory
        shutil.rmtree(self.temporary_path, ignore_errors = True)

    # Method for generating the file .npy in the appropriate directory
    # save_path --- the location to which you want to place a file with the result
    # biomes_instead_lst --- do you need to get a landscape type matrix instead of an LST matrix
    def archive_to_npy(self, save_path, biomes_instead_lst = False):
        self.biomes_instead_lst = biomes_instead_lst

        if os.path.isdir(save_path) == False:
            os.mkdir(save_path)
        warpOptions, imageVRTPath = self.__preparation()

        geotiff_name = self.datetime + '.tif'
        geotiff_path = os.path.join(self.temporary_path, geotiff_name)
        raster = gdal.Warp(geotiff_path, imageVRTPath, dstNodata = self.key_values.get('NoData'), options = warpOptions)

        # Saving the matrix in .npy format
        if self.biomes_instead_lst == True:
            npy_name = self.datetime + '_biomes.npy'
        else:
            npy_name = self.datetime + '.npy'
        npy_path = os.path.join(save_path, npy_name)
        matrix = raster.ReadAsArray()
        matrix = np.array(matrix)
        np.save(npy_path, matrix)

        raster = None
        # Deleting the temporary directory
        shutil.rmtree(self.temporary_path, ignore_errors = True)

    # Method for saving metadata to a JSON file
    # output_path --- the location to which you want to place a file with the result
    def save_metadata(self, output_path):
        with open(output_path, 'w') as f:
            f.write(json.dumps(self.metadata))