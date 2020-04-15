"""
Based on: https://blog.paperspace.com/tag/series-yolo/
"""

from __future__ import division

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import numpy as np
import cv2


def predict_transform(prediction, input_dim, anchors, num_classes, cuda):
    """ Takes a detection feature map and turns it into a 2-D tensor """
    batch_size = prediction.size(0)
    grid_size = prediction.size(2)
    stride = input_dim // grid_size
    bbox_attrs = 5 + num_classes # (x, y, w, h, objectness) + class probabilities
    num_anchors = len(anchors)

    prediction = prediction.view(batch_size, bbox_attrs*num_anchors, grid_size*grid_size)
    prediction = prediction.transpose(1,2).contiguous()
    prediction = prediction.view(batch_size, grid_size*grid_size*num_anchors, bbox_attrs)

    # Toggle CUDA
    if cuda:
        FloatTensor = torch.cuda.FloatTensor
    else:
        FloatTensor = torch.FloatTensor

    # Use sigmoid function on x,y coordinates to constrain x,y offsets between 0 and 1
    prediction[:,:,0] = torch.sigmoid(prediction[:,:,0])
    prediction[:,:,1] = torch.sigmoid(prediction[:,:,1])

    # Use sigmoid function on objectness score to get a probability
    prediction[:,:,4] = torch.sigmoid(prediction[:,:,4])

    # Apply sigmoid activation to class scores
    prediction[:,:,5:] = torch.sigmoid((prediction[:,:,5:]))

    # Add grid offsets to the center coordinates prediction
    grid = np.arange(grid_size)
    grid_x, grid_y = np.meshgrid(grid, grid)

    x_offset = FloatTensor(grid_x).view(-1,1)
    y_offset = FloatTensor(grid_y).view(-1,1)
    x_y_offset = torch.cat((x_offset, y_offset), 1).repeat(1, num_anchors).view(-1,2).unsqueeze(0)

    prediction[:,:,:2] += x_y_offset

    # Divide anchors by the stride of the detection feature map
    anchors = FloatTensor([(a_w/stride, a_h/stride) for a_w, a_h in anchors])

    # Apply anchors to dimensions of bounding box
    anchors = anchors.repeat(grid_size*grid_size, 1).unsqueeze(0)
    prediction[:,:,2:4] = torch.exp(prediction[:,:,2:4])*anchors

    # Resize the detections map to the size of the input image
    prediction[:,:,:4] *= stride

    return prediction

def non_max_suppression(prediction, conf_threshold, num_classes, nms_threshold=0.4):
    """ Applies thresholding based on objectness score and non-maximum suppression """
    # Object confidence thresholding
    conf_mask = (prediction[:,:,4] > confidence).float().unsqueeze(2)
    prediction = prediction*conf_mask

    # Non-maximum suppression thresholding
    # Transform (center x, center y, height, width) attributes of the bounding boxes to 
    # (top-left corner x, top-left corner y, right-bottom corner x, right-bottom corner y)
    box_corner = prediction.new(prediction.shape)
    box_corner[:,:,0] = (prediction[:,:,0] - prediction[:,:,2] / 2)
    box_corner[:,:,1] = (prediction[:,:,1] - prediction[:,:,3] / 2)
    box_corner[:,:,2] = (prediction[:,:,0] + prediction[:,:,2] / 2)
    box_corner[:,:,3] = (prediction[:,:,1] + prediction[:,:,3] / 2)

    flag = False
    
    # Loop over images in a batch
    for index in range(box_corner.size(0)):
        img_pred = box_corner[index]

        # Get index of class with the highest value and its score
        class_conf, class_score = torch.max(img_pred[:,5:], 1) # Offset by 5
        class_conf = class_conf.float().unsqueeze(1)
        class_score = class_score.float().unsqueeze(1)
        img_pred = torch.cat((img_pred[:,:5], class_conf, class_score), 1)

         # Get rid of zeros
        non_zero_idx = (torch.nonzero(img_pred[:,4]))

        try:
            img_pred = img_pred[non_zero_idx.squeeze(),:].view(-1,7)
        except:
            continue
        
        if img_pred.shape[0] == 0:
            continue 

        # Get unique classes detected in an image
        img_classes = img_pred[:,-1].unique()

        # Perform non-maximum suppression class-wise
        for cl in img_classes:
            # Get the detections for one class
            cl_mask = img_pred*(img_pred[:,-1] == cl).float().unsqueeze(1)
            cl_mask_idx = torch.nonzero(cl_mask[:,-2]).squeeze()
            img_pred_class = img_pred[cl_mask_idx].view(-1,7)

            # Sort detections by objectness score in descending order
            conf_sort_idx = torch.sort(img_pred_class[:,4], descending=True)[1]
            img_pred_class = img_pred_class[conf_sort_idx]
            idx = img_pred_class.size(0) # Number of detections

            for i in range(idx):
                # Get IOUs of all later bounding boxes
                try:
                    ious = bbox_iou(img_pred_class[i].unsqueeze(0), img_pred_class[i+1:])

                except ValueError:
                    break

                except IndexError:
                    break

                # Zero out all the detections that have IoU > treshhold
                iou_mask = (ious < nms_conf).float().unsqueeze(1)
                img_pred_class[i+1:] *= iou_mask       

                # Remove the non-zero entries
                non_zero_idx = torch.nonzero(img_pred_class[:,4]).squeeze()
                img_pred_class = img_pred_class[non_zero_idx].view(-1,7)
            
            batch_idx = img_pred_class.new(img_pred_class.size(0), 1).fill_(idx)      
            
            # Repeat the batch_id for as many detections of the class cl in the image
            seq = batch_idx, img_pred_class

            if not flag:
                output = torch.cat(seq, 1)
                write = True
            else:
                out = torch.cat(seq,1)
                output = torch.cat((output, out))
    
    try:
        return output
    except:
        return 0

def bbox_iou(box1, box2):
    """
    Returns the IoU of two bounding boxes  
    """
    # Get the coordinates of the bounding boxes
    b1_x1, b1_y1, b1_x2, b1_y2 = box1[:,0], box1[:,1], box1[:,2], box1[:,3]
    b2_x1, b2_y1, b2_x2, b2_y2 = box2[:,0], box2[:,1], box2[:,2], box2[:,3]
    
    # Get the coordinates of the intersection rectangle
    inter_rect_x1 =  torch.max(b1_x1, b2_x1)
    inter_rect_y1 =  torch.max(b1_y1, b2_y1)
    inter_rect_x2 =  torch.min(b1_x2, b2_x2)
    inter_rect_y2 =  torch.min(b1_y2, b2_y2)
    
    # Intersection area
    inter_area = torch.clamp(inter_rect_x2 - inter_rect_x1 + 1, min=0) * torch.clamp(inter_rect_y2 - inter_rect_y1 + 1, min=0)
 
    # Union area
    b1_area = (b1_x2 - b1_x1 + 1)*(b1_y2 - b1_y1 + 1)
    b2_area = (b2_x2 - b2_x1 + 1)*(b2_y2 - b2_y1 + 1)
    
    iou = inter_area / (b1_area + b2_area - inter_area)
    
    return iou