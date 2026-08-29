from django.db import migrations, models
import documents.models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0004_require_vector_reingestion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="document",
            name="file",
            field=models.FileField(
                max_length=500,
                upload_to=documents.models.document_upload_path,
            ),
        ),
    ]
