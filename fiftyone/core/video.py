"""
Video frame views.

| Copyright 2017-2022, Voxel51, Inc.
| `voxel51.com <https://voxel51.com/>`_
|
"""
from collections import defaultdict
from copy import deepcopy
import logging
import os

from bson import ObjectId
from pymongo import UpdateOne

import eta.core.utils as etau

import fiftyone as fo
import fiftyone.core.dataset as fod
import fiftyone.core.fields as fof
import fiftyone.core.media as fom
import fiftyone.core.sample as fos
import fiftyone.core.odm as foo
import fiftyone.core.odm.sample as foos
import fiftyone.core.utils as fou
import fiftyone.core.validation as fova
import fiftyone.core.view as fov

fouv = fou.lazy_import("fiftyone.utils.video")


logger = logging.getLogger(__name__)


class FrameView(fos.SampleView):
    """A frame in a :class:`FramesView`.

    :class:`FrameView` instances should not be created manually; they are
    generated by iterating over :class:`FramesView` instances.

    Args:
        doc: a :class:`fiftyone.core.odm.DatasetSampleDocument`
        view: the :class:`FramesView` that the frame belongs to
        selected_fields (None): a set of field names that this view is
            restricted to
        excluded_fields (None): a set of field names that are excluded from
            this view
        filtered_fields (None): a set of field names of list fields that are
            filtered in this view
    """

    @property
    def _sample_id(self):
        return ObjectId(self._doc.sample_id)

    def save(self):
        """Saves the frame to the database."""
        super().save()
        self._view._sync_source_sample(self)


class FramesView(fov.DatasetView):
    """A :class:`fiftyone.core.view.DatasetView` of frames from a video
    :class:`fiftyone.core.dataset.Dataset`.

    Frames views contain an ordered collection of frames, each of which
    corresponds to a single frame of a video from the source collection.

    Frames retrieved from frames views are returned as :class:`FrameView`
    objects.

    Args:
        source_collection: the
            :class:`fiftyone.core.collections.SampleCollection` from which this
            view was created
        frames_stage: the :class:`fiftyone.core.stages.ToFrames` stage that
            defines how the frames were created
        frames_dataset: the :class:`fiftyone.core.dataset.Dataset` that serves
            the frames in this view
    """

    def __init__(
        self, source_collection, frames_stage, frames_dataset, _stages=None
    ):
        if _stages is None:
            _stages = []

        self._source_collection = source_collection
        self._frames_stage = frames_stage
        self._frames_dataset = frames_dataset
        self.__stages = _stages

    def __copy__(self):
        return self.__class__(
            self._source_collection,
            deepcopy(self._frames_stage),
            self._frames_dataset,
            _stages=deepcopy(self.__stages),
        )

    @property
    def _base_view(self):
        return self.__class__(
            self._source_collection, self._frames_stage, self._frames_dataset,
        )

    @property
    def _dataset(self):
        return self._frames_dataset

    @property
    def _root_dataset(self):
        return self._source_collection._root_dataset

    @property
    def _sample_cls(self):
        return FrameView

    @property
    def _stages(self):
        return self.__stages

    @property
    def _all_stages(self):
        return (
            self._source_collection.view()._all_stages
            + [self._frames_stage]
            + self.__stages
        )

    @property
    def name(self):
        return self.dataset_name + "-frames"

    def _get_default_sample_fields(
        self, include_private=False, use_db_fields=False
    ):
        fields = super()._get_default_sample_fields(
            include_private=include_private, use_db_fields=use_db_fields
        )

        if use_db_fields:
            return fields + ("_sample_id", "frame_number")

        return fields + ("sample_id", "frame_number")

    def _get_default_indexes(self, frames=False):
        if frames:
            return super()._get_default_indexes(frames=frames)

        return ["id", "filepath", "sample_id", "_sample_id_1_frame_number_1"]

    def set_values(self, field_name, *args, **kwargs):
        # The `set_values()` operation could change the contents of this view,
        # so we first record the sample IDs that need to be synced
        if self._stages:
            ids = self.values("id")
        else:
            ids = None

        super().set_values(field_name, *args, **kwargs)

        field = field_name.split(".", 1)[0]
        self._sync_source(fields=[field], ids=ids)

    def save(self, fields=None):
        """Saves the frames in this view to the underlying dataset.

        .. note::

            This method is not a :class:`fiftyone.core.stages.ViewStage`;
            it immediately writes the requested changes to the underlying
            dataset.

        .. warning::

            This will permanently delete any omitted or filtered contents from
            the frames of the source dataset.

        Args:
            fields (None): an optional field or list of fields to save. If
                specified, only these fields are overwritten
        """
        if etau.is_str(fields):
            fields = [fields]

        super().save(fields=fields)

        self._sync_source(fields=fields)

    def keep(self):
        """Deletes all frames that are **not** in this view from the underlying
        dataset.

        .. note::

            This method is not a :class:`fiftyone.core.stages.ViewStage`;
            it immediately writes the requested changes to the underlying
            dataset.
        """

        # The `keep()` operation below will delete frames, so we must sync
        # deletions to the source dataset first
        self._sync_source(update=False, delete=True)

        super().keep()

    def keep_fields(self):
        """Deletes any sample fields that have been excluded in this view from
        the frames of the underlying dataset.

        .. note::

            This method is not a :class:`fiftyone.core.stages.ViewStage`;
            it immediately writes the requested changes to the underlying
            dataset.
        """
        self._sync_source_keep_fields()

        super().keep_fields()

    def reload(self):
        """Reloads this view from the source collection in the database.

        Note that :class:`FrameView` instances are not singletons, so any
        in-memory frames extracted from this view will not be updated by
        calling this method.
        """
        self._source_collection.reload()

        #
        # Regenerate the frames dataset
        #
        # This assumes that calling `load_view()` when the current patches
        # dataset has been deleted will cause a new one to be generated
        #

        self._frames_dataset.delete()
        _view = self._frames_stage.load_view(self._source_collection)
        self._frames_dataset = _view._frames_dataset

    def _set_labels(self, field_name, sample_ids, label_docs):
        super()._set_labels(field_name, sample_ids, label_docs)

        self._sync_source(fields=[field_name], ids=sample_ids)

    def _delete_labels(self, ids, fields=None):
        super()._delete_labels(ids, fields=fields)

        if fields is not None:
            if etau.is_str(fields):
                fields = [fields]

            frame_fields = [
                self._source_collection._FRAMES_PREFIX + f for f in fields
            ]
        else:
            frame_fields = None

        self._source_collection._delete_labels(ids, fields=frame_fields)

    def _sync_source_sample(self, sample):
        self._sync_source_schema()

        default_fields = set(
            self._get_default_sample_fields(
                include_private=True, use_db_fields=True
            )
        )

        updates = {
            k: v
            for k, v in sample.to_mongo_dict().items()
            if k not in default_fields
        }

        if not updates:
            return

        match = {
            "_sample_id": sample._sample_id,
            "frame_number": sample.frame_number,
        }

        self._source_collection._dataset._frame_collection.update_one(
            match, {"$set": updates}
        )

    def _sync_source(self, fields=None, ids=None, update=True, delete=False):
        default_fields = set(
            self._get_default_sample_fields(
                include_private=True, use_db_fields=True
            )
        )

        if fields is not None:
            fields = [f for f in fields if f not in default_fields]
            if not fields:
                return

        if update:
            self._sync_source_schema(fields=fields)

            dst_coll = self._source_collection._dataset._frame_collection_name

            pipeline = []

            if ids is not None:
                pipeline.append(
                    {
                        "$match": {
                            "_id": {"$in": [ObjectId(_id) for _id in ids]}
                        }
                    }
                )

            if fields is None:
                default_fields.discard("_sample_id")
                default_fields.discard("frame_number")

                pipeline.append({"$unset": list(default_fields)})
            else:
                project = {f: True for f in fields}
                project["_id"] = True
                project["_sample_id"] = True
                project["frame_number"] = True
                pipeline.append({"$project": project})

            pipeline.append(
                {
                    "$merge": {
                        "into": dst_coll,
                        "on": ["_sample_id", "frame_number"],
                        "whenMatched": "merge",
                        "whenNotMatched": "discard",
                    }
                }
            )

            self._frames_dataset._aggregate(pipeline=pipeline)

        if delete:
            frame_ids = self._frames_dataset.exclude(self).values("id")
            self._source_collection._dataset._clear_frames(frame_ids=frame_ids)

    def _sync_source_schema(self, fields=None, delete=False):
        schema = self.get_field_schema()
        src_schema = self._source_collection.get_frame_field_schema()

        add_fields = []
        del_fields = []

        if fields is not None:
            # We're syncing specific fields; if they are not present in source
            # collection, add them

            for field_name in fields:
                if field_name not in src_schema:
                    add_fields.append(field_name)
        else:
            # We're syncing all fields; add any missing fields to source
            # collection and, if requested, delete any source fields that
            # aren't in this view

            default_fields = set(
                self._get_default_sample_fields(include_private=True)
            )

            for field_name in schema.keys():
                if (
                    field_name not in src_schema
                    and field_name not in default_fields
                ):
                    add_fields.append(field_name)

            if delete:
                for field_name in src_schema.keys():
                    if field_name not in schema:
                        del_fields.append(field_name)

        for field_name in add_fields:
            field_kwargs = foo.get_field_kwargs(schema[field_name])
            self._source_collection._dataset.add_frame_field(
                field_name, **field_kwargs
            )

        if delete:
            for field_name in del_fields:
                self._source_collection._dataset.delete_frame_field(field_name)

    def _sync_source_keep_fields(self):
        schema = self.get_field_schema()
        src_schema = self._source_collection.get_frame_field_schema()

        del_fields = set(src_schema.keys()) - set(schema.keys())
        if del_fields:
            prefix = self._source_collection._FRAMES_PREFIX
            _del_fields = [prefix + f for f in del_fields]
            self._source_collection.exclude_fields(_del_fields).keep_fields()


def make_frames_dataset(
    sample_collection,
    sample_frames=False,
    fps=None,
    max_fps=None,
    size=None,
    min_size=None,
    max_size=None,
    sparse=False,
    frames_patt=None,
    force_sample=False,
    skip_failures=True,
    verbose=False,
):
    """Creates a dataset that contains one sample per frame in the video
    collection.

    The returned dataset will contain all frame-level fields and the ``tags``
    of each video as sample-level fields, as well as a ``sample_id`` field that
    records the IDs of the parent sample for each frame.

    By default, ``sample_frames`` is False and this method assumes that the
    frames of the input collection have ``filepath`` fields populated pointing
    to each frame image. Any frames without a ``filepath`` populated will be
    omitted from the frames dataset.

    When ``sample_frames`` is True, this method samples each video in the
    collection into a directory of per-frame images with the same basename as
    the input video with frame numbers/format specified by ``frames_patt``, and
    stores the resulting frame paths in a ``filepath`` field of the input
    collection.

    For example, if ``frames_patt = "%%06d.jpg"``, then videos with the
    following paths::

        /path/to/video1.mp4
        /path/to/video2.mp4
        ...

    would be sampled as follows::

        /path/to/video1/
            000001.jpg
            000002.jpg
            ...
        /path/to/video2/
            000001.jpg
            000002.jpg
            ...

    By default, samples will be generated for every video frame at full
    resolution, but this method provides a variety of parameters that can be
    used to customize the sampling behavior.

    .. note::

        If this method is run multiple times with ``sample_frames`` set to
        True, existing frames will not be resampled unless you set
        ``force_sample`` to True.

    .. note::

        The returned dataset is independent from the source collection;
        modifying it will not affect the source collection.

    Args:
        sample_collection: a
            :class:`fiftyone.core.collections.SampleCollection`
        sample_frames (False): whether to assume that the frame images have
            already been sampled at locations stored in the ``filepath`` field
            of each frame (False), or whether to sample the video frames now
            according to the specified parameters (True)
        fps (None): an optional frame rate at which to sample each video's
            frames
        max_fps (None): an optional maximum frame rate at which to sample.
            Videos with frame rate exceeding this value are downsampled
        size (None): an optional ``(width, height)`` at which to sample frames.
            A dimension can be -1, in which case the aspect ratio is preserved.
            Only applicable when ``sample_frames=True``
        min_size (None): an optional minimum ``(width, height)`` for each
            frame. A dimension can be -1 if no constraint should be applied.
            The frames are resized (aspect-preserving) if necessary to meet
            this constraint. Only applicable when ``sample_frames=True``
        max_size (None): an optional maximum ``(width, height)`` for each
            frame. A dimension can be -1 if no constraint should be applied.
            The frames are resized (aspect-preserving) if necessary to meet
            this constraint. Only applicable when ``sample_frames=True``
        sparse (False): whether to only sample frame images for frame numbers
            for which :class:`fiftyone.core.frame.Frame` instances exist in the
            input collection. This parameter has no effect when
            ``sample_frames==False`` since frames must always exist in order to
            have ``filepath`` information use
        frames_patt (None): a pattern specifying the filename/format to use to
            write or check or existing sampled frames, e.g., ``"%%06d.jpg"``.
            The default value is
            ``fiftyone.config.default_sequence_idx + fiftyone.config.default_image_ext``
        force_sample (False): whether to resample videos whose sampled frames
            already exist. Only applicable when ``sample_frames=True``
        skip_failures (True): whether to gracefully continue without raising
            an error if a video cannot be sampled
        verbose (False): whether to log information about the frames that will
            be sampled, if any

    Returns:
        a :class:`fiftyone.core.dataset.Dataset`
    """
    fova.validate_video_collection(sample_collection)

    if sample_frames != True:
        l = locals()
        for var in ("size", "min_size", "max_size"):
            if l[var]:
                logger.warning(
                    "Ignoring '%s' when sample_frames=%s", var, sample_frames
                )

    if frames_patt is None:
        frames_patt = (
            fo.config.default_sequence_idx + fo.config.default_image_ext
        )

    #
    # Create dataset with proper schema
    #

    dataset = fod.Dataset(_frames=True)
    dataset._doc.app_sidebar_groups = (
        sample_collection._dataset._doc.app_sidebar_groups
    )
    dataset.media_type = fom.IMAGE
    dataset.add_sample_field(
        "sample_id", fof.ObjectIdField, db_field="_sample_id"
    )

    frame_schema = sample_collection.get_frame_field_schema()
    dataset._sample_doc_cls.merge_field_schema(frame_schema)

    dataset.create_index("sample_id")

    # This index will be used when populating the collection now as well as
    # later when syncing the source collection
    dataset.create_index([("sample_id", 1), ("frame_number", 1)], unique=True)

    _make_pretty_summary(dataset)

    # Initialize frames dataset
    ids_to_sample, frames_to_sample = _init_frames(
        dataset,
        sample_collection,
        sample_frames,
        frames_patt,
        fps,
        max_fps,
        sparse,
        force_sample,
        verbose,
    )

    # Sample frames, if necessary
    if ids_to_sample:
        logger.info("Sampling video frames...")
        to_sample_view = sample_collection._root_dataset.select(
            ids_to_sample, ordered=True
        )
        fouv.sample_videos(
            to_sample_view,
            frames_patt=frames_patt,
            frames=frames_to_sample,
            size=size,
            min_size=min_size,
            max_size=max_size,
            original_frame_numbers=True,
            force_sample=True,
            save_filepaths=True,
            skip_failures=skip_failures,
        )

    # Merge frame data
    pipeline = sample_collection._pipeline(frames_only=True)

    if sample_frames == "dynamic":
        pipeline.append({"$unset": "filepath"})

    pipeline.append(
        {
            "$merge": {
                "into": dataset._sample_collection_name,
                "on": ["_sample_id", "frame_number"],
                "whenMatched": "merge",
                "whenNotMatched": "discard",
            }
        }
    )

    sample_collection._dataset._aggregate(pipeline=pipeline)

    # Delete samples for frames without filepaths
    if sample_frames == True:
        dataset._sample_collection.delete_many({"filepath": None})

    if sample_frames == False and not dataset:
        logger.warning(
            "Your frames view is empty. Note that you must either "
            "pre-populate the `filepath` field on the frames of your video "
            "collection or pass `sample_frames=True` to this method to "
            "perform the sampling. See "
            "https://voxel51.com/docs/fiftyone/user_guide/using_views.html#frame-views "
            "for more information."
        )

    return dataset


def _make_pretty_summary(dataset):
    set_fields = ["id", "sample_id", "filepath", "frame_number"]
    all_fields = dataset._sample_doc_cls._fields_ordered
    pretty_fields = set_fields + [f for f in all_fields if f not in set_fields]
    dataset._sample_doc_cls._fields_ordered = tuple(pretty_fields)


def _init_frames(
    dataset,
    src_collection,
    sample_frames,
    frames_patt,
    fps,
    max_fps,
    sparse,
    force_sample,
    verbose,
):
    if (
        (sample_frames != False and not sparse)
        or fps is not None
        or max_fps is not None
    ):
        # We'll need frame counts to determine what frames to include/sample
        src_collection.compute_metadata()

    if sample_frames == True and verbose:
        logger.info("Determining frames to sample...")

    #
    # Initialize frames dataset with proper frames
    #

    docs = []
    src_docs = []
    src_inds = []
    missing_filepaths = []

    id_map = {}
    sample_map = defaultdict(set)
    frame_map = defaultdict(set)

    src_dataset = src_collection._root_dataset
    is_clips = src_collection._dataset._is_clips
    if src_collection.has_frame_field("filepath"):
        view = src_collection.select_fields("frames.filepath")
    else:
        view = src_collection.select_fields()

    for sample in view._aggregate(attach_frames=True):
        video_path = sample["filepath"]
        tags = sample.get("tags", [])
        metadata = sample.get("metadata", None) or {}
        frame_rate = metadata.get("frame_rate", None)
        total_frame_count = metadata.get("total_frame_count", -1)
        frames = sample.get("frames", [])

        frame_ids_map = {}
        frames_with_filepaths = set()
        for frame in frames:
            _frame_id = frame["_id"]
            fn = frame["frame_number"]
            filepath = frame.get("filepath", None)

            if sample_frames != False or filepath:
                frame_ids_map[fn] = _frame_id

            if sample_frames == True and filepath:
                frames_with_filepaths.add(fn)

        if is_clips:
            _sample_id = sample["_sample_id"]
            support = sample["support"]
        else:
            _sample_id = sample["_id"]
            support = None

        outdir = os.path.splitext(video_path)[0]
        images_patt = os.path.join(outdir, frames_patt)

        # Determine which frame numbers to include in the frames dataset and
        # whether any frame images need to be sampled
        doc_frame_numbers, sample_frame_numbers = _parse_video_frames(
            video_path,
            sample_frames,
            images_patt,
            support,
            total_frame_count,
            frame_rate,
            frame_ids_map,
            force_sample,
            sparse,
            fps,
            max_fps,
            verbose,
        )

        # Record things that need to be sampled
        # Note: [] means no frames, None means all frames
        if sample_frame_numbers != []:
            id_map[video_path] = str(_sample_id)

            if sample_frame_numbers is None:
                sample_map[video_path] = None
            elif sample_map[video_path] is not None:
                sample_map[video_path].update(sample_frame_numbers)

        # Record any already-sampled frames whose `filepath` need to be stored
        # on the source dataset
        if sample_frames == True and sample_frame_numbers is not None:
            missing_fns = (
                set(doc_frame_numbers)
                - set(sample_frame_numbers)
                - frames_with_filepaths
            )
        else:
            missing_fns = set()

        for fn in missing_fns:
            missing_filepaths.append((_sample_id, fn, images_patt % fn))

        # Create necessary frame documents
        for fn in doc_frame_numbers:
            if is_clips:
                fns = frame_map[video_path]
                if fn in fns:
                    continue  # frame has already been sampled

                fns.add(fn)

            _id = frame_ids_map.get(fn, None)

            if sample_frames == "dynamic":
                filepath = video_path
            else:
                filepath = None  # will be populated later

            doc = {
                "filepath": filepath,
                "tags": tags,
                "metadata": None,
                "frame_number": fn,
                "_media_type": "image",
                "_rand": foos._generate_rand(images_patt % fn),
                "_sample_id": _sample_id,
            }

            if _id is not None:
                doc["_id"] = _id
            elif fn in missing_fns:
                # Found a frame whose image is already sampled but for which
                # there is no frame in the source collection. We now need to
                # create a frame so that the missing filepath can be added and
                # the frames dataset can use the same frame ID
                src_docs.append({"_sample_id": _sample_id, "frame_number": fn})
                src_inds.append(len(docs))

            docs.append(doc)

            # Commit batch of docs to frames dataset
            if len(docs) >= 100000:  # MongoDB limit for bulk inserts
                _insert_docs(docs, src_docs, src_inds, dataset, src_dataset)

    # Add remaining docs to frames dataset
    _insert_docs(docs, src_docs, src_inds, dataset, src_dataset)

    # Add missing frame filepaths to source collection
    if missing_filepaths:
        logger.info(
            "Setting %d frame filepaths on the input collection that exist "
            "on disk but are not recorded on the dataset",
            len(missing_filepaths),
        )
        src_dataset.add_frame_field("filepath", fof.StringField)
        ops = [
            UpdateOne(
                {"_sample_id": _sample_id, "frame_number": fn},
                {"$set": {"filepath": filepath}},
            )
            for _sample_id, fn, filepath in missing_filepaths
        ]
        src_dataset._bulk_write(ops, frames=True)

    #
    # Finalize which frame images need to be sampled, if any
    #
    # We first populate `sample_map` and then convert to `ids_to_sample` and
    # `frames_to_sample` here to avoid resampling frames when working with clip
    # views with multiple overlapping clips into the same video
    ids_to_sample = []
    frames_to_sample = []
    for video_path, sample_frame_numbers in sample_map.items():
        ids_to_sample.append(id_map[video_path])
        if sample_frame_numbers is not None:
            sample_frame_numbers = sorted(sample_frame_numbers)

        frames_to_sample.append(sample_frame_numbers)

    return ids_to_sample, frames_to_sample


def _insert_docs(docs, src_docs, src_inds, dataset, src_dataset):
    if src_docs:
        foo.insert_documents(src_docs, src_dataset._frame_collection)

        for idx, src_doc in enumerate(src_docs):
            docs[src_inds[idx]]["_id"] = src_doc["_id"]

        src_docs.clear()
        src_inds.clear()

    if docs:
        foo.insert_documents(docs, dataset._sample_collection)
        docs.clear()


def _parse_video_frames(
    video_path,
    sample_frames,
    images_patt,
    support,
    total_frame_count,
    frame_rate,
    frame_ids_map,
    force_sample,
    sparse,
    fps,
    max_fps,
    verbose,
):
    #
    # Determine target frames, taking subsampling into account
    #

    if fps is not None or max_fps is not None:
        target_frame_numbers = fouv.sample_frames_uniform(
            frame_rate,
            total_frame_count=total_frame_count,
            support=support,
            fps=fps,
            max_fps=max_fps,
        )
    elif support is not None:
        first, last = support
        target_frame_numbers = list(range(first, last + 1))
    else:
        target_frame_numbers = None  # all frames

    #
    # Determine frames for which to generate documents
    #

    if target_frame_numbers is None:
        if total_frame_count < 0:
            doc_frame_numbers = sorted(frame_ids_map.keys())
        else:
            doc_frame_numbers = list(range(1, total_frame_count + 1))
    else:
        doc_frame_numbers = target_frame_numbers

    if sparse or sample_frames == False:
        doc_frame_numbers = [
            fn for fn in doc_frame_numbers if fn in frame_ids_map
        ]

    if sample_frames != True:
        return doc_frame_numbers, []

    #
    # Determine frames that need to be sampled
    #

    if force_sample:
        sample_frame_numbers = doc_frame_numbers
    else:
        sample_frame_numbers = _get_non_existent_frame_numbers(
            images_patt, doc_frame_numbers
        )

    if (
        target_frame_numbers is None
        and len(sample_frame_numbers) == len(doc_frame_numbers)
        and len(doc_frame_numbers) >= total_frame_count
    ):
        sample_frame_numbers = None  # all frames

    if verbose:
        count = total_frame_count if total_frame_count >= 0 else "???"
        if sample_frame_numbers is None:
            logger.info(
                "Must sample all %s frames of '%s'", count, video_path,
            )
        elif sample_frame_numbers != []:
            logger.info(
                "Must sample %d/%s frames of '%s'",
                len(sample_frame_numbers),
                count,
                video_path,
            )
        else:
            logger.info("Required frames already present for '%s'", video_path)

    return doc_frame_numbers, sample_frame_numbers


def _get_non_existent_frame_numbers(images_patt, frame_numbers):
    return [fn for fn in frame_numbers if not os.path.isfile(images_patt % fn)]
