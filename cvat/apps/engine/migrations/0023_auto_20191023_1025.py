# Generated by Django 2.2.4 on 2019-10-23 10:25

import os
import re
import shutil
import glob
import logging
import sys

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings

from cvat.apps.engine.media_extractors import (VideoReader, ArchiveReader, ZipReader,
    PdfReader , ImageListReader, Mpeg4ChunkWriter,
    ZipChunkWriter, ZipCompressedChunkWriter, get_mime)
from cvat.apps.engine.models import DataChoice

def create_data_objects(apps, schema_editor):
    def fix_path(path):
        ind = path.find('.upload')
        if ind != -1:
            path = path[ind + len('.upload') + 1:]
        return path

    def get_frame_step(frame_filter):
        match = re.search("step\s*=\s*([1-9]\d*)", frame_filter)
        return int(match.group(1)) if match else 1

    migration_name = os.path.splitext(os.path.basename(__file__))[0]
    migration_log_file = '{}.log'.format(migration_name)
    stdout = sys.stdout
    # redirect all stdout to the file
    log_file = open(os.path.join(settings.MIGRATIONS_LOGS_ROOT, migration_log_file), 'w')
    sys.stdout = log_file
    sys.stderr = log_file

    log = logging.getLogger(migration_name)
    log.addHandler(logging.StreamHandler(stdout))
    log.addHandler(logging.StreamHandler(log_file))
    log.setLevel(logging.INFO)

    Task = apps.get_model('engine', 'Task')
    Data = apps.get_model('engine', 'Data')

    db_tasks = list(Task.objects.prefetch_related("image_set").select_related("video").all())
    task_count = len(db_tasks)
    log.info('\nStart data migration...')
    for task_idx, db_task in enumerate(db_tasks):
        progress = (100 * task_idx) // task_count
        log.info('Start migration of task ID {}. Progress: {}% | {}/{}.'.format(db_task.id, progress, task_idx + 1, task_count))
        try:
            # create folders
            new_task_dir = os.path.join(settings.TASKS_ROOT, str(db_task.id))
            os.makedirs(new_task_dir)
            os.makedirs(os.path.join(new_task_dir, 'artifacts'))
            new_task_logs_dir = os.path.join(new_task_dir, 'logs')
            os.makedirs(new_task_logs_dir)

            # create Data object
            db_data = Data.objects.create(
                size=db_task.size,
                image_quality=db_task.image_quality,
                start_frame=db_task.start_frame,
                stop_frame=db_task.stop_frame,
                frame_filter=db_task.frame_filter,
                compressed_chunk_type = DataChoice.IMAGESET,
                original_chunk_type = DataChoice.VIDEO if db_task.mode == 'interpolation' else DataChoice.IMAGESET,
            )
            db_data.save()

            db_task.data = db_data

            db_data_dir = os.path.join(settings.MEDIA_DATA_ROOT, str(db_data.id))
            os.makedirs(db_data_dir)
            compressed_cache_dir = os.path.join(db_data_dir, 'compressed')
            os.makedirs(compressed_cache_dir)

            original_cache_dir = os.path.join(db_data_dir, 'original')
            os.makedirs(original_cache_dir)

            old_db_task_dir = os.path.join(settings.DATA_ROOT, str(db_task.id))

            # prepare media data
            old_task_data_dir = os.path.join(old_db_task_dir, 'data')
            if os.path.exists(old_task_data_dir):
                if hasattr(db_task, 'video'):
                    if os.path.exists(db_task.video.path):
                        reader = VideoReader([db_task.video.path], get_frame_step(db_data.frame_filter), db_data.start_frame, db_data.stop_frame)
                        original_chunk_writer = Mpeg4ChunkWriter(100)
                        compressed_chunk_writer = ZipCompressedChunkWriter(db_data.image_quality)

                        for chunk_idx, chunk_images in enumerate(reader.slice_by_size(db_data.chunk_size)):
                            original_chunk_path = os.path.join(original_cache_dir, '{}.mp4'.format(chunk_idx))
                            original_chunk_writer.save_as_chunk(chunk_images, original_chunk_path)

                            compressed_chunk_path = os.path.join(compressed_cache_dir, '{}.zip'.format(chunk_idx))
                            compressed_chunk_writer.save_as_chunk(chunk_images, compressed_chunk_path)

                        reader.save_preview(os.path.join(db_data_dir, 'preview.jpeg'))
                    else:
                        log.error('No raw video data found for task {}'.format(db_task.id))
                else:
                    original_images = [os.path.realpath(db_image.path) for db_image in db_task.image_set.all()]
                    reader = None
                    if os.path.exists(original_images[0]): # task created from images
                        reader = ImageListReader(original_images)
                    else: # task created from archive or pdf
                        archives = []
                        pdfs = []
                        zips = []
                        for p in glob.iglob(os.path.join(old_db_task_dir, '.upload', '**', '*'), recursive=True):
                            mime_type = get_mime(p)
                            if mime_type == 'archive':
                                archives.append(p)
                            elif mime_type == 'pdf':
                                pdfs.append(p)
                            elif mime_type == 'zip':
                                zips.append(p)
                        if archives:
                            reader = ArchiveReader(archives, get_frame_step(db_data.frame_filter), db_data.start_frame, db_data.stop_frame)
                        elif zips:
                            reader = ZipReader(archives, get_frame_step(db_data.frame_filter), db_data.start_frame, db_data.stop_frame)
                        elif pdfs:
                            reader = PdfReader(pdfs, get_frame_step(db_data.frame_filter), db_data.start_frame, db_data.stop_frame)

                    if not reader:
                        log.error('No raw data found for task {}'.format(db_task.id))
                    else:
                        original_chunk_writer = ZipChunkWriter(100)
                        compressed_chunk_writer = ZipCompressedChunkWriter(db_data.image_quality)

                        for chunk_idx, chunk_images in enumerate(reader.slice_by_size(db_data.chunk_size)):
                            compressed_chunk_path = os.path.join(compressed_cache_dir, '{}.zip'.format(chunk_idx))
                            compressed_chunk_writer.save_as_chunk(chunk_images, compressed_chunk_path)

                            original_chunk_path = os.path.join(original_cache_dir, '{}.zip'.format(chunk_idx))
                            original_chunk_writer.save_as_chunk(chunk_images, original_chunk_path)

                        reader.save_preview(os.path.join(db_data_dir, 'preview.jpeg'))

            # move logs
            for log_file in ('task.log', 'client.log'):
                task_log_file = os.path.join(old_db_task_dir, log_file)
                if os.path.isfile(task_log_file):
                    shutil.move(task_log_file, new_task_logs_dir)

            if hasattr(db_task, 'video'):
                db_task.video.data = db_data
                db_task.video.path = fix_path(db_task.video.path)
                db_task.video.save()

            for db_image in db_task.image_set.all():
                db_image.data = db_data
                db_image.path = fix_path(db_image.path)
                db_image.save()

            old_raw_dir = os.path.join(old_db_task_dir, '.upload')
            new_raw_dir = os.path.join(db_data_dir, 'raw')

            for client_file in db_task.clientfile_set.all():
                client_file.file = client_file.file.path.replace(old_raw_dir, new_raw_dir)
                client_file.save()

            for server_file in db_task.serverfile_set.all():
                server_file.file = server_file.file.replace(old_raw_dir, new_raw_dir)
                server_file.save()

            for remote_file in db_task.remotefile_set.all():
                remote_file.file = remote_file.file.replace(old_raw_dir, new_raw_dir)
                remote_file.save()

            db_task.save()

            #move old raw data
            if os.path.exists(old_db_task_dir):
                shutil.move(old_raw_dir, new_raw_dir)

        except Exception as e:
            log.error('Cannot migrate data for the task: {}'.format(db_task.id))
            log.error(str(e))

    # DL models migration
    if apps.is_installed('auto_annotation'):
        DLModel = apps.get_model('auto_annotation', 'AnnotationModel')

        for db_model in DLModel.objects.all():
            try:
                old_location = os.path.join(settings.BASE_DIR, 'models', str(db_model.id))
                new_location = os.path.join(settings.BASE_DIR, 'data', 'models', str(db_model.id))

                if os.path.isdir(old_location):
                    shutil.move(old_location, new_location)

                    db_model.model_file.name = db_model.model_file.name.replace(old_location, new_location)
                    db_model.weights_file.name = db_model.weights_file.name.replace(old_location, new_location)
                    db_model.labelmap_file.name = db_model.labelmap_file.name.replace(old_location, new_location)
                    db_model.interpretation_file.name = db_model.interpretation_file.name.replace(old_location, new_location)

                    db_model.save()
            except Exception as e:
                log.error('Cannot migrate data for the DL model: {}'.format(db_model.id))
                log.error(str(e))

    sys.stdout.close()

class Migration(migrations.Migration):

    dependencies = [
        ('engine', '0022_auto_20191004_0817'),
    ]

    operations = [
        migrations.CreateModel(
            name='Data',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chunk_size', models.PositiveIntegerField(default=36)),
                ('size', models.PositiveIntegerField(default=0)),
                ('image_quality', models.PositiveSmallIntegerField(default=50)),
                ('start_frame', models.PositiveIntegerField(default=0)),
                ('stop_frame', models.PositiveIntegerField(default=0)),
                ('frame_filter', models.CharField(blank=True, default='', max_length=256)),
                ('compressed_chunk_type', models.CharField(choices=[('video', 'VIDEO'), ('imageset', 'IMAGESET'), ('list', 'LIST')], default=DataChoice('imageset'), max_length=32)),
                ('original_chunk_type', models.CharField(choices=[('video', 'VIDEO'), ('imageset', 'IMAGESET'), ('list', 'LIST')], default=DataChoice('imageset'), max_length=32)),
            ],
            options={
                'default_permissions': (),
            },
        ),
        migrations.AddField(
            model_name='task',
            name='data',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='engine.Data'),
        ),
        migrations.AddField(
            model_name='image',
            name='data',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='images', to='engine.Data'),
        ),
        migrations.AddField(
            model_name='video',
            name='data',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='video', to='engine.Data'),
        ),
        migrations.AddField(
            model_name='clientfile',
            name='data',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='client_files', to='engine.Data'),
        ),
        migrations.AddField(
            model_name='remotefile',
            name='data',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='remote_files', to='engine.Data'),
        ),
        migrations.AddField(
            model_name='serverfile',
            name='data',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='server_files', to='engine.Data'),
        ),
        migrations.RunPython(
            code=create_data_objects
        ),
        migrations.RemoveField(
            model_name='image',
            name='task',
        ),
        migrations.RemoveField(
            model_name='remotefile',
            name='task',
        ),
        migrations.RemoveField(
            model_name='serverfile',
            name='task',
        ),
        migrations.RemoveField(
            model_name='task',
            name='frame_filter',
        ),
        migrations.RemoveField(
            model_name='task',
            name='image_quality',
        ),
        migrations.RemoveField(
            model_name='task',
            name='size',
        ),
        migrations.RemoveField(
            model_name='task',
            name='start_frame',
        ),
        migrations.RemoveField(
            model_name='task',
            name='stop_frame',
        ),
        migrations.RemoveField(
            model_name='video',
            name='task',
        ),
        migrations.AlterField(
            model_name='image',
            name='path',
            field=models.CharField(default='', max_length=1024),
        ),
        migrations.AlterField(
            model_name='video',
            name='path',
            field=models.CharField(default='', max_length=1024),
        ),
        migrations.AlterUniqueTogether(
            name='clientfile',
            unique_together={('data', 'file')},
        ),
        migrations.RemoveField(
            model_name='clientfile',
            name='task',
        ),
    ]
