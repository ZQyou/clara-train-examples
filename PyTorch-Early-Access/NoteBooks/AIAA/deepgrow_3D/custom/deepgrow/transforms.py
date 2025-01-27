# Copyright 2020 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
A collection of "vanilla" transforms for spatial operations
https://github.com/Project-MONAI/MONAI/wiki/MONAI_Design
"""
import json
from typing import Optional, Sequence, Union

import numpy as np

from monai.config import KeysCollection
from monai.transforms import Resize, SpatialCrop
from monai.transforms.compose import MapTransform, Randomizable, Transform
from monai.transforms.spatial.dictionary import InterpolateModeSequence
from monai.transforms.utils import generate_spatial_bounding_box
from monai.utils import InterpolateMode, ensure_tuple_rep, min_version, optional_import

measure, _ = optional_import("skimage.measure", "0.14.2", min_version)
distance_transform_cdt, _ = optional_import("scipy.ndimage.morphology", name="distance_transform_cdt")
gaussian_filter, _ = optional_import("scipy.ndimage", name="gaussian_filter")


class FindAllValidSlicesd(Transform):
    def __init__(self, label="label", sids="sids"):
        self.label = label
        self.sids = sids

    def _apply(self, label):
        if len(label.shape) != 4:  # only for 3D
            return None

        sids = []
        for sid in range(label.shape[1]):  # Assume channel is first
            if np.sum(label[0][sid]) == 0:
                continue
            sids.append(sid)
        return np.asarray(sids)

    def __call__(self, data):
        sids = self._apply(data[self.label])
        if sids is not None and len(sids):
            data[self.sids] = sids
        return data


class AddInitialSeedPointd(Randomizable, Transform):
    def __init__(self, label="label", guidance="guidance", sids="sids", sid="sid", connected_regions=6):
        self.label = label
        self.sids = sids
        self.sid = sid
        self.guidance = guidance
        self.connected_regions = connected_regions

    def randomize(self, data=None):
        pass

    def _apply(self, label, sid):
        dimensions = 3 if len(label.shape) > 3 else 2
        default_guidance = [-1] * (dimensions + 1)

        dims = dimensions
        if sid is not None and dimensions == 3:
            dims = 2
            label = label[0][sid][np.newaxis]  # Assume channel is first

        label = (label > 0.5).astype(np.float32)
        blobs_labels = measure.label(label.astype(int), background=0) if dims == 2 else label
        assert np.max(blobs_labels) > 0, "Not a valid Label"

        pos_guidance = []
        for ridx in range(1, 2 if dims == 3 else self.connected_regions):
            if dims == 2:
                label = (blobs_labels == ridx).astype(np.float32)
                if np.sum(label) == 0:
                    pos_guidance.append(default_guidance)
                    continue

            distance = distance_transform_cdt(label).flatten()
            probability = np.exp(distance) - 1.0

            idx = np.where(label.flatten() > 0)[0]
            seed = self.R.choice(idx, size=1, p=probability[idx] / np.sum(probability[idx]))
            dst = distance[seed]

            g = np.asarray(np.unravel_index(seed, label.shape)).transpose().tolist()[0]
            g[0] = dst[0]
            if dimensions == 2 or dims == 3:
                pos_guidance.append(g)
            else:
                pos_guidance.append([g[0], sid, g[-2], g[-1]])

        return np.asarray([pos_guidance, [default_guidance] * len(pos_guidance)])

    def __call__(self, data):
        sid = data.get(self.sid)
        sids = data.get(self.sids)
        if sids is not None:
            if sid is None or sid not in sids:
                sid = self.R.choice(sids, replace=False)
        else:
            sid = None
        data[self.guidance] = self._apply(data[self.label], sid)
        return data


class AddGuidanceSignald(Transform):
    def __init__(self, image="image", guidance="guidance", sigma=2, number_intensity_ch=1, batched=False):
        self.image = image
        self.guidance = guidance
        self.sigma = sigma
        self.number_intensity_ch = number_intensity_ch
        self.batched = batched

    def _get_signal(self, image, guidance):
        dimensions = 3 if len(image.shape) > 3 else 2
        guidance = guidance.tolist() if isinstance(guidance, np.ndarray) else guidance
        if dimensions == 3:
            signal = np.zeros((len(guidance), image.shape[-3], image.shape[-2], image.shape[-1]), dtype=np.float32)
        else:
            signal = np.zeros((len(guidance), image.shape[-2], image.shape[-1]), dtype=np.float32)

        sshape = signal.shape
        for i in range(len(guidance)):
            for point in guidance[i]:
                if np.any(np.asarray(point) < 0):
                    continue

                if dimensions == 3:
                    p1 = max(0, min(int(point[-3]), sshape[-3] - 1))
                    p2 = max(0, min(int(point[-2]), sshape[-2] - 1))
                    p3 = max(0, min(int(point[-1]), sshape[-1] - 1))
                    signal[i, p1, p2, p3] = 1.0
                else:
                    p1 = max(0, min(int(point[-2]), sshape[-2] - 1))
                    p2 = max(0, min(int(point[-1]), sshape[-1] - 1))
                    signal[i, p1, p2] = 1.0

            if np.max(signal[i]) > 0:
                signal[i] = gaussian_filter(signal[i], sigma=self.sigma)
                signal[i] = (signal[i] - np.min(signal[i])) / (np.max(signal[i]) - np.min(signal[i]))
        return signal

    def _apply(self, image, guidance):
        if not self.batched:
            signal = self._get_signal(image, guidance)
            return np.concatenate([image, signal], axis=0)

        images = []
        for i, g in zip(image, guidance):
            i = i[0 : 0 + self.number_intensity_ch, ...]
            signal = self._get_signal(i, g)
            images.append(np.concatenate([i, signal], axis=0))
        return images

    def __call__(self, data):
        image = data[self.image]
        guidance = data[self.guidance]

        data[self.image] = self._apply(image, guidance)
        return data


class FindDiscrepancyRegionsd(Transform):
    def __init__(self, label="label", pred="pred", discrepancy="discrepancy", batched=True):
        self.label = label
        self.pred = pred
        self.discrepancy = discrepancy
        self.batched = batched

    @staticmethod
    def disparity(label, pred):
        label = (label > 0.5).astype(np.float32)
        pred = (pred > 0.5).astype(np.float32)
        disparity = label - pred

        pos_disparity = (disparity > 0).astype(np.float32)
        neg_disparity = (disparity < 0).astype(np.float32)
        return [pos_disparity, neg_disparity]

    def _apply(self, label, pred):
        if not self.batched:
            return self.disparity(label, pred)

        disparity = []
        for la, pr in zip(label, pred):
            disparity.append(self.disparity(la, pr))
        return disparity

    def __call__(self, data):
        label = data[self.label]
        pred = data[self.pred]

        data[self.discrepancy] = self._apply(label, pred)
        return data


class AddRandomGuidanced(Randomizable, Transform):
    def __init__(self, guidance="guidance", discrepancy="discrepancy", probability="probability", batched=True):
        self.guidance = guidance
        self.discrepancy = discrepancy
        self.probability = probability
        self.batched = batched

    def randomize(self, data=None):
        pass

    @staticmethod
    def find_guidance(discrepancy):
        distance = distance_transform_cdt(discrepancy).flatten()
        probability = np.exp(distance) - 1.0
        idx = np.where(discrepancy.flatten() > 0)[0]

        if np.sum(discrepancy > 0) > 0:
            seed = np.random.choice(idx, size=1, p=probability[idx] / np.sum(probability[idx]))
            dst = distance[seed]

            g = np.asarray(np.unravel_index(seed, discrepancy.shape)).transpose().tolist()[0]
            g[0] = dst[0]
            return g
        return None

    @staticmethod
    def add_guidance(discrepancy, probability):
        will_interact = np.random.choice([True, False], p=[probability, 1.0 - probability])
        if not will_interact:
            return None, None

        pos_discr = discrepancy[0]
        neg_discr = discrepancy[1]

        can_be_positive = np.sum(pos_discr) > 0
        can_be_negative = np.sum(neg_discr) > 0
        correct_pos = np.sum(pos_discr) >= np.sum(neg_discr)

        if correct_pos and can_be_positive:
            return AddRandomGuidanced.find_guidance(pos_discr), None

        if not correct_pos and can_be_negative:
            return None, AddRandomGuidanced.find_guidance(neg_discr)
        return None, None

    def _apply(self, guidance, discrepancy, probability):
        guidance = guidance.tolist() if isinstance(guidance, np.ndarray) else guidance
        if not self.batched:
            pos, neg = self.add_guidance(discrepancy, probability)
            if pos:
                guidance[0].append(pos)
                guidance[1].append([-1] * len(pos))
            if neg:
                guidance[0].append([-1] * len(neg))
                guidance[1].append(neg)
        else:
            for g, d, p in zip(guidance, discrepancy, probability):
                pos, neg = self.add_guidance(d, p)
                if pos:
                    g[0].append(pos)
                    g[1].append([-1] * len(pos))
                if neg:
                    g[0].append([-1] * len(neg))
                    g[1].append(neg)
        return np.asarray(guidance)

    def __call__(self, data):
        guidance = data[self.guidance]
        discrepancy = data[self.discrepancy]
        probability = data[self.probability]

        data[self.guidance] = self._apply(guidance, discrepancy, probability)
        return data


class SpatialCropForegroundd(MapTransform):
    def __init__(
        self,
        keys,
        source_key: str,
        spatial_size,
        select_fn=lambda x: x > 0,
        channel_indices=None,
        margin: int = 0,
        meta_key_postfix="meta_dict",
        start_coord_key: str = "foreground_start_coord",
        end_coord_key: str = "foreground_end_coord",
        original_shape_key: str = "foreground_original_shape",
        cropped_shape_key: str = "foreground_cropped_shape",
    ) -> None:
        super().__init__(keys)

        self.source_key = source_key
        self.spatial_size = list(spatial_size)
        self.select_fn = select_fn
        self.channel_indices = channel_indices
        self.margin = margin
        self.meta_key_postfix = meta_key_postfix
        self.start_coord_key = start_coord_key
        self.end_coord_key = end_coord_key
        self.original_shape_key = original_shape_key
        self.cropped_shape_key = cropped_shape_key

    def __call__(self, data):
        box_start, box_end = generate_spatial_bounding_box(
            data[self.source_key], self.select_fn, self.channel_indices, self.margin
        )

        center = np.mean([box_start, box_end], axis=0).astype(int).tolist()
        current_size = np.subtract(box_end, box_start).astype(int).tolist()

        if np.all(np.less(current_size, self.spatial_size)):
            cropper = SpatialCrop(roi_center=center, roi_size=self.spatial_size)
            box_start = cropper.roi_start
            box_end = cropper.roi_end
        else:
            cropper = SpatialCrop(roi_start=box_start, roi_end=box_end)

        for key in self.keys:
            meta_key = f"{key}_{self.meta_key_postfix}"
            data[meta_key][self.start_coord_key] = box_start
            data[meta_key][self.end_coord_key] = box_end
            data[meta_key][self.original_shape_key] = data[key].shape

            image = cropper(data[key])
            data[meta_key][self.cropped_shape_key] = image.shape
            data[key] = image
        return data


# Transforms to support Inference
class SpatialCropGuidanced(MapTransform):
    def __init__(
        self,
        keys,
        guidance: str,
        spatial_size,
        spatial_size_key: str = "spatial_size",
        margin: int = 20,
        meta_key_postfix="meta_dict",
        start_coord_key: str = "foreground_start_coord",
        end_coord_key: str = "foreground_end_coord",
        original_shape_key: str = "foreground_original_shape",
        cropped_shape_key: str = "foreground_cropped_shape",
    ) -> None:
        super().__init__(keys)

        self.guidance = guidance
        self.spatial_size = list(spatial_size)
        self.spatial_size_key = spatial_size_key
        self.margin = margin
        self.meta_key_postfix = meta_key_postfix
        self.start_coord_key = start_coord_key
        self.end_coord_key = end_coord_key
        self.original_shape_key = original_shape_key
        self.cropped_shape_key = cropped_shape_key

    def bounding_box(self, points, img_shape):
        ndim = len(img_shape)
        margin = ensure_tuple_rep(self.margin, ndim)
        for m in margin:
            if m < 0:
                raise ValueError("margin value should not be negative number.")

        box_start = [0] * ndim
        box_end = [0] * ndim

        for di in range(ndim):
            dt = points[..., di]
            min_d = max(min(dt - margin[di]), 0)
            max_d = min(img_shape[di] - 1, max(dt + margin[di]))
            box_start[di], box_end[di] = min_d, max_d
        return box_start, box_end

    def __call__(self, data):
        guidance = data[self.guidance]
        box_start = None
        for key in self.keys:
            box_start, box_end = self.bounding_box(np.array(guidance[0] + guidance[1]), data[key].shape[1:])
            center = np.mean([box_start, box_end], axis=0).astype(int).tolist()
            spatial_size = data.get(self.spatial_size_key, self.spatial_size)

            current_size = np.absolute(np.subtract(box_start, box_end)).astype(int).tolist()
            spatial_size = spatial_size[-len(current_size) :]
            if len(spatial_size) < len(current_size):  # 3D spatial_size = [256,256] (include all slices in such case)
                diff = len(current_size) - len(spatial_size)
                spatial_size = list(data[key].shape[1 : (1 + diff)]) + spatial_size

            if np.all(np.less(current_size, spatial_size)):
                if len(center) == 3:
                    center[0] = center[0] + (spatial_size[0] // 2 - center[0])
                cropper = SpatialCrop(roi_center=center, roi_size=spatial_size)
            else:
                cropper = SpatialCrop(roi_start=box_start, roi_end=box_end)
            box_start, box_end = cropper.roi_start, cropper.roi_end

            meta_key = f"{key}_{self.meta_key_postfix}"
            data[meta_key][self.start_coord_key] = box_start
            data[meta_key][self.end_coord_key] = box_end
            data[meta_key][self.original_shape_key] = data[key].shape

            image = cropper(data[key])
            data[meta_key][self.cropped_shape_key] = image.shape
            data[key] = image

        pos_clicks, neg_clicks = guidance[0], guidance[1]
        pos = np.subtract(pos_clicks, box_start).tolist() if len(pos_clicks) else []
        neg = np.subtract(neg_clicks, box_start).tolist() if len(neg_clicks) else []

        data[self.guidance] = [pos, neg]
        return data


class ResizeGuidanced(Transform):
    def __init__(
        self,
        guidance: str,
        ref_image,
        meta_key_postfix="meta_dict",
        cropped_shape_key: str = "foreground_cropped_shape",
    ) -> None:
        self.guidance = guidance
        self.ref_image = ref_image
        self.meta_key_postfix = meta_key_postfix
        self.cropped_shape_key = cropped_shape_key

    def __call__(self, data):
        guidance = data[self.guidance]
        meta_dict = data[f"{self.ref_image}_{self.meta_key_postfix}"]
        current_shape = data[self.ref_image].shape[1:]
        cropped_shape = meta_dict[self.cropped_shape_key][1:]
        factor = np.divide(current_shape, cropped_shape)

        pos_clicks, neg_clicks = guidance[0], guidance[1]
        pos = np.multiply(pos_clicks, factor).astype(int).tolist() if len(pos_clicks) else []
        neg = np.multiply(neg_clicks, factor).astype(int).tolist() if len(neg_clicks) else []

        data[self.guidance] = [pos, neg]
        return data


class RestoreCroppedLabeld(MapTransform):
    def __init__(
        self,
        keys: KeysCollection,
        ref_image: str,
        slice_only=False,
        channel_first=True,
        mode: InterpolateModeSequence = InterpolateMode.NEAREST,
        align_corners: Union[Sequence[Optional[bool]], Optional[bool]] = None,
        meta_key_postfix: str = "meta_dict",
        start_coord_key: str = "foreground_start_coord",
        end_coord_key: str = "foreground_end_coord",
        original_shape_key: str = "foreground_original_shape",
        cropped_shape_key: str = "foreground_cropped_shape",
    ) -> None:
        super().__init__(keys)
        self.ref_image = ref_image
        self.slice_only = slice_only
        self.channel_first = channel_first
        self.mode = ensure_tuple_rep(mode, len(self.keys))
        self.align_corners = ensure_tuple_rep(align_corners, len(self.keys))
        self.meta_key_postfix = meta_key_postfix
        self.start_coord_key = start_coord_key
        self.end_coord_key = end_coord_key
        self.original_shape_key = original_shape_key
        self.cropped_shape_key = cropped_shape_key

    def __call__(self, data):
        meta_dict = data[f"{self.ref_image}_{self.meta_key_postfix}"]

        for idx, key in enumerate(self.keys):
            image = data[key]

            # Undo Resize
            current_size = image.shape
            cropped_size = meta_dict[self.cropped_shape_key]
            if np.any(np.not_equal(current_size, cropped_size)):
                resizer = Resize(spatial_size=cropped_size[1:], mode=self.mode[idx])
                image = resizer(image, mode=self.mode[idx], align_corners=self.align_corners[idx])

            # Undo Crop
            original_shape = meta_dict[self.original_shape_key]
            result = np.zeros(original_shape, dtype=np.float32)
            box_start = meta_dict[self.start_coord_key]
            box_end = meta_dict[self.end_coord_key]

            sd = min(len(box_start), len(box_end), len(image.shape[1:]))  # spatial dims
            slices = [slice(None)] + [slice(s, e) for s, e in zip(box_start[:sd], box_end[:sd])]
            slices = tuple(slices)
            result[slices] = image

            # Undo Spacing
            current_size = result.shape[1:]
            spatial_shape = np.roll(meta_dict["spatial_shape"], 1).tolist()
            spatial_size = spatial_shape[-len(current_size) :]

            if np.any(np.not_equal(current_size, spatial_size)):
                resizer = Resize(spatial_size=spatial_size, mode=self.mode[idx])
                result = resizer(result, mode=self.mode[idx], align_corners=self.align_corners[idx])

            # Undo Slicing
            slice_idx = meta_dict.get("slice_idx")
            if slice_idx is None or self.slice_only:
                final_result = result if len(result.shape) <= 3 else result[0]
            else:
                slice_idx = meta_dict["slice_idx"][0]
                final_result = np.zeros(spatial_shape)
                if self.channel_first:
                    final_result[slice_idx] = result
                else:
                    final_result[..., slice_idx] = result
            data[key] = final_result

            meta = data.get(f"{key}_{self.meta_key_postfix}")
            if meta is None:
                meta = dict()
                data[f"{key}_{self.meta_key_postfix}"] = meta
            meta["slice_idx"] = slice_idx
            meta["affine"] = meta_dict["original_affine"]
        return data


class AddGuidanceFromPointsd(Randomizable, Transform):
    def __init__(
        self,
        ref_image,
        guidance="guidance",
        foreground="foreground",
        background="background",
        axis=0,
        channel_first=True,
        dimensions=2,
        slice_key="slice",
        meta_key_postfix: str = "meta_dict",
    ):
        self.ref_image = ref_image
        self.guidance = guidance
        self.foreground = foreground
        self.background = background
        self.axis = axis
        self.channel_first = channel_first
        self.dimensions = dimensions
        self.slice_key = slice_key
        self.meta_key_postfix = meta_key_postfix

    def randomize(self, data=None):
        pass

    def _apply(self, pos_clicks, neg_clicks, factor, slice_num=None):
        points = pos_clicks
        points.extend(neg_clicks)
        points = np.array(points)

        if self.dimensions == 2:
            slices = np.unique(points[:, self.axis]).tolist()
            slice_idx = slices[0] if slice_num is None else next(x for x in slices if x == slice_num)

            pos = neg = []
            if len(pos_clicks):
                pos_clicks = np.array(pos_clicks)
                pos = (pos_clicks[np.where(pos_clicks[:, self.axis] == slice_idx)] * factor)[:, 1:].astype(int).tolist()
            if len(neg_clicks):
                neg_clicks = np.array(neg_clicks)
                neg = (neg_clicks[np.where(neg_clicks[:, self.axis] == slice_idx)] * factor)[:, 1:].astype(int).tolist()

            guidance = [pos, neg, slice_idx, factor]
        else:
            pos = neg = []
            if len(pos_clicks):
                pos = np.multiply(pos_clicks, factor).astype(int).tolist()
            if len(neg_clicks):
                neg = np.multiply(neg_clicks, factor).astype(int).tolist()
            guidance = [pos, neg]
        return guidance

    def __call__(self, data):
        meta_dict = data[f"{self.ref_image}_{self.meta_key_postfix}"]
        original_shape = meta_dict["spatial_shape"]
        current_shape = list(data[self.ref_image].shape)

        clicks = [data[self.foreground], data[self.background]]
        if self.channel_first:
            original_shape = np.roll(original_shape, 1).tolist()
            for i in range(len(clicks)):
                clicks[i] = json.loads(clicks[i]) if isinstance(clicks[i], str) else clicks[i]
                clicks[i] = np.array(clicks[i]).astype(int).tolist()
                for j in range(len(clicks[i])):
                    clicks[i][j] = np.roll(clicks[i][j], 1).tolist()

        factor = np.array(current_shape) / original_shape
        data[self.guidance] = self._apply(clicks[0], clicks[1], factor, data.get(self.slice_key))
        return data


class Fetch2DSliced(MapTransform):
    def __init__(self, keys, guidance="guidance", axis=0, meta_key_postfix: str = "meta_dict"):
        super().__init__(keys)
        self.guidance = guidance
        self.axis = axis
        self.meta_key_postfix = meta_key_postfix

    def _apply(self, image, guidance):
        slice_idx = guidance[2]  # (pos, neg, slice_idx, factor)
        idx = []
        for i in range(len(image.shape)):
            idx.append(slice_idx) if i == self.axis else idx.append(slice(0, image.shape[i]))

        idx = tuple(idx)
        return image[idx], idx

    def __call__(self, data):
        guidance = data[self.guidance]
        for key in self.keys:
            img, idx = self._apply(data[key], guidance)
            data[key] = img
            data[f"{key}_{self.meta_key_postfix}"]["slice_idx"] = idx
        return data
