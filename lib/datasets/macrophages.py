# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ismail Elezi and Lukas Tuggener based on Ross Girshick and Xinlei Chen's code for pascal_voc dataset
# --------------------------------------------------------
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import pandas as pa
from datasets.imdb import imdb
import datasets.ds_utils as ds_utils
import xml.etree.ElementTree as ET
import numpy as np
import scipy.sparse
import scipy.io as sio
import utils.bbox
import pickle
import subprocess
import uuid
from datasets.voc_eval import voc_eval
from datasets.voc_eval import parse_rec_dota
from main.config import cfg
import random
import math


class macrophages(imdb):
  def __init__(self, image_set, year, devkit_path=None):
    imdb.__init__(self, 'macrophages' + year + '_' + image_set)
    self._year = year
    self._image_set = image_set
    self._devkit_path = self._get_default_path() if devkit_path is None \
      else devkit_path
    self._data_path = os.path.join(self._devkit_path, 'train')
    #self._split_path = os.path.join(self._devkit_path, 'train_val_test')
    # no fixed split randomly according to size
    self._val_proportion = 0.1
    self._classes = list(["background", "DAPI", "mCherry"])

    self._class_to_ind = dict(list(zip(self.classes, list(range(self.num_classes)))))
    self._image_ext = '.tif'
    self._image_index = self._load_image_set_index()
    # Default to roidb handler
    self._roidb_handler = self.gt_roidb
    self._salt = str(uuid.uuid4())
    self._comp_id = 'comp4'

    # PASCAL specific config options
    self.config = {'cleanup': True,
                   'use_salt': True,
                   'use_diff': False,
                   'matlab_eval': False,
                   'rpn_file': None}

    assert os.path.exists(self._devkit_path), \
      'VOCdevkit path does not exist: {}'.format(self._devkit_path)
    assert os.path.exists(self._data_path), \
      'Path does not exist: {}'.format(self._data_path)
    assert os.path.exists(self._data_path), \
      'Path does not exist: {}'.format(self._split_path)

  def image_path_at(self, i):
    """
    Return the absolute path to image i in the image sequence.
    """
    return self.image_path_from_index(self._image_index[i])

  def image_path_from_index(self, index):
    """
    Construct an image path from the image's "index" identifier.
    """
    image_path = os.path.join(self._data_path, 'images',
                              index + self._image_ext)
    assert os.path.exists(image_path), \
      'Path does not exist: {}'.format(image_path)
    return image_path

  def _load_image_set_index(self):
    """
    Load the indexes listed in this dataset's image set file.
    """
    # Example path to image set file:
    # image_set_file =  os.path.join(self._devkit_path, "VOCdevkit2007/VOC2007/ImageSets/Main/val.txt")
    images_path = os.path.join(self._data_path, 'images')
    assert os.path.exists(images_path), \
      'Path does not exist: {}'.format(images_path)

    images = os.listdir(images_path)

    # #read according file
    # with open(self._split_path+"/"+self._image_set+".txt") as f:
    #   allowed_names = f.readlines()
    #
    # allowed_names = [x.strip() for x in allowed_names]

    # strip extension - only use one of the pairs
    images = [x[:-4] for x in images if "DAPI" in x]

    # intersection of existing and allowed files
    #image_index = list(set(allowed_names).intersection(images))
    image_index = images

    return image_index

  def _get_default_path(self):
    """
    Return the default path where PASCAL VOC is expected to be installed.
    """
    return os.path.join(cfg.DATA_DIR, 'macrophages'+"_" + self._year)

  def gt_roidb(self):
    """
    Return the database of ground-truth regions of interest.

    This function loads/saves from/to a cache file to speed up future calls.
    """
    cache_file = os.path.join(self.cache_path, self.name + '_gt_roidb.pkl')
    #if os.path.exists(cache_file):
    if False:
      with open(cache_file, 'rb') as fid:
        try:
          roidb = pickle.load(fid)
        except:
          roidb = pickle.load(fid, encoding='bytes')
      print('{} gt roidb loaded from {}'.format(self.name, cache_file))
      return roidb

    gt_roidb = [self._load_musical_annotation(index)
                for index in self.image_index]
    with open(cache_file, 'wb') as fid:
      pickle.dump(gt_roidb, fid, pickle.HIGHEST_PROTOCOL)
    print('wrote gt roidb to {}'.format(cache_file))

    return gt_roidb

  def rpn_roidb(self):
    if int(self._year) == 2018 or self._image_set != 'debug':
      gt_roidb = self.gt_roidb()
      rpn_roidb = self._load_rpn_roidb(gt_roidb)
      roidb = imdb.merge_roidbs(gt_roidb, rpn_roidb)
    else:
      roidb = self._load_rpn_roidb(None)

    return roidb

  def _load_rpn_roidb(self, gt_roidb):
    filename = self.config['rpn_file']
    print('loading {}'.format(filename))
    assert os.path.exists(filename), \
      'rpn data not found at: {}'.format(filename)
    with open(filename, 'rb') as f:
      box_list = pickle.load(f)
    return self.create_roidb_from_box_list(box_list, gt_roidb)

  def _load_musical_annotation(self, index):
    """
    Load bounding box information form microphages data (from file names)
    """
    img_nr, _ = index.split("_")

    img_classes = ["DAPI", "mCherry"]
    annotations = []

    for img_class in img_classes:
      dir_name = os.path.join(self._data_path, 'object_masks', img_nr)
      masks = os.listdir(dir_name)
      objs = [x for x in masks if img_class in x]

      # semseg path
      semseg_path = os.path.join(self._data_path, 'masks', img_nr+"_"+img_class+".tif")

      # object seg paths
      objseg_path = [os.path.join(self._data_path, 'object_masks', img_nr, x) for x in objs]


      # bboxes
      num_objs = len(objs)

      boxes = np.zeros((num_objs, 4), dtype=np.uint16)
      gt_classes = np.zeros((num_objs), dtype=np.int32)
      overlaps = np.zeros((num_objs, self.num_classes), dtype=np.float32)
      # "Seg" area for pascal is just the box area
      seg_areas = np.zeros((num_objs), dtype=np.float32)

      for ix, obj in enumerate(objs):
        obj = obj.split("_")
        x1 = int(obj[0])
        y1 = int(obj[1])
        x2 = int(obj[2])+x1
        y2 = int(obj[3])+y1
        cls = self._class_to_ind[img_class]
        boxes[ix, :] = [x1, y1, x2, y2]
        gt_classes[ix] = cls
        overlaps[ix, cls] = 1.0
        seg_areas[ix] = (x2 - x1 + 1) * (y2 - y1 + 1)

      overlaps = scipy.sparse.csr_matrix(overlaps)


      annotations.append({'boxes': boxes,
              'gt_classes': gt_classes,
              'gt_overlaps': overlaps,
              'flipped': False,
              'seg_areas': seg_areas,
              'semseg_path': semseg_path,
              'objseg_path': objseg_path})

    return annotations


  def _get_comp_id(self):
    comp_id = (self._comp_id + '_' + self._salt if self.config['use_salt']
               else self._comp_id)
    return comp_id

  def _get_voc_results_file_template(self):
    filename = self._get_comp_id() + '_det_' + self._image_set + '_{:s}.txt'
    path = os.path.join(
      self._devkit_path,
      'results',
      'dota' + self._year,
      'Main',
      filename)
    return path

  def _write_voc_results_file(self, all_boxes):
   for cls_ind, cls in enumerate(self.classes):
      if cls == '__background__':
        continue
      print('Writing {} VOC results file'.format(cls))
      filename = self._get_voc_results_file_template().format(cls)
      with open(filename, 'wt') as f:
        for im_ind, index in enumerate(self.image_index):
          dets = all_boxes[cls_ind][im_ind]
          if dets == []:
            continue
          # the VOCdevkit expects 1-based indices
          for k in range(dets.shape[0]):
            f.write('{:s} {:.3f} {:.1f} {:.1f} {:.1f} {:.1f}\n'.
                    format(index, dets[k, -1],
                           dets[k, 0] + 1, dets[k, 1] + 1,
                           dets[k, 2] + 1, dets[k, 3] + 1))

  def _do_python_eval(self, output_dir='output', path=None):
    annopath = os.path.join(
      self._devkit_path,
      'segmentation_detection',
      'xml_annotations',
      '{:s}.txt')
    imagesetfile = os.path.join(
      self._devkit_path,
      'train_val_test',
      self._image_set + '.txt')
    cachedir = os.path.join(self._devkit_path, 'annotations_cache')
    # The PASCAL VOC metric changed in 2010
    use_07_metric = True if int(self._year) < 2010 else False
    print('VOC07 metric? ' + ('Yes' if use_07_metric else 'No'))
    if not os.path.isdir(output_dir):
      os.mkdir(output_dir)

    ovthresh_list = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    for ovthresh in ovthresh_list:
      aps = []
      for i, cls in enumerate(self._classes):
        if cls == '__background__':
          continue
        filename = self._get_voc_results_file_template().format(cls)
        rec, prec, ap = voc_eval(
          filename, annopath, imagesetfile, cls, cachedir, ovthresh=ovthresh,
          use_07_metric=use_07_metric)
        aps += [ap]
        print(('AP for {} = {:.4f}'.format(cls, ap)))
        with open(os.path.join(output_dir, cls + '_pr.pkl'), 'wb') as f:
          pickle.dump({'rec': rec, 'prec': prec, 'ap': ap}, f)
      print(('Mean AP = {:.4f}'.format(np.mean(aps))))
      print('~~~~~~~~')
      print('Results:')
      # open the file where we want to save the results
      if path is not None:
        res_file = open(os.path.join('/DeepWatershedDetection' + path, 'res-' + str(ovthresh) + '.txt'),"w+")
        len_ap = len(aps)
        sum_aps = 0
        present = 0
        for i in range(len_ap):
          print(('{:.3f}'.format(aps[i])))
          if math.isnan(aps[i]):
            res_file.write(str(0) + "\n")
          else:
            res_file.write(('{:.3f}'.format(aps[i])) + "\n")
            sum_aps += aps[i]
          present += 1
        res_file.write('\n\n\n')
        res_file.write("Mean Average Precision: " + str(sum_aps / float(present)))
        res_file.close()

      print(('{:.3f}'.format(np.mean(aps))))
    print('~~~~~~~~')
    print('')
    print('--------------------------------------------------------------')
    print('Results computed with the **unofficial** Python eval code.')
    print('Results should be very close to the official MATLAB eval code.')
    print('Recompute with `./tools/reval.py --matlab ...` for your paper.')
    print('-- Thanks, The Management')
    print('--------------------------------------------------------------')

  def _do_matlab_eval(self, output_dir='output'):
    print('-----------------------------------------------------')
    print('Computing results with the official MATLAB eval code.')
    print('-----------------------------------------------------')
    path = os.path.join(cfg.ROOT_DIR, 'lib', 'datasets',
                        'VOCdevkit-matlab-wrapper')
    cmd = 'cd {} && '.format(path)
    cmd += '{:s} -nodisplay -nodesktop '.format(cfg.MATLAB)
    cmd += '-r "dbstop if error; '
    cmd += 'voc_eval(\'{:s}\',\'{:s}\',\'{:s}\',\'{:s}\'); quit;"' \
      .format(self._devkit_path, self._get_comp_id(),
              self._image_set, output_dir)
    print(('Running:\n{}'.format(cmd)))
    status = subprocess.call(cmd, shell=True)

  def evaluate_detections(self, all_boxes, output_dir, path=None):
    self._write_voc_results_file(all_boxes)
    self._do_python_eval(output_dir, path)
    if self.config['matlab_eval']:
      self._do_matlab_eval(output_dir)
    if self.config['cleanup']:
      for cls in self._classes:
        if cls == '__background__':
          continue
        filename = self._get_voc_results_file_template().format(cls)
        os.remove(filename)

  def competition_mode(self, on):
    if on:
      self.config['use_salt'] = False
      self.config['cleanup'] = False
    else:
      self.config['use_salt'] = True
      self.config['cleanup'] = True


if __name__ == '__main__':

  d = macrophages('trainval', '2018')
  res = d.roidb

