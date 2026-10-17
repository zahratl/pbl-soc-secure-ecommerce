import json
import re

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    IntegerField,
    TextAreaField,
    SelectField,
    HiddenField,
    MultipleFileField,
    BooleanField
)
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Regexp
)


# Daftar ukuran pakaian
SIZES = ('XS', 'S', 'M', 'L', 'XL', 'XXL')

# Daftar subkategori produk
SUBCATEGORIES = (
    'Outer',
    'Skirts',
    'Pants',
    'Shirts',
    'T-Shirts',
    'Knit'
)


class ProductForm(FlaskForm):

    # IDENTITAS PRODUK
    name = StringField(
        'Nama Produk',
        validators=[
            DataRequired(message='Nama produk wajib diisi.'),
            Length(max=200)
        ]
    )

    sku = StringField(
        'SKU / Nomor Artikel',
        validators=[
            DataRequired(message='SKU wajib diisi.'),
            Length(max=64),
            Regexp(
                r'^[A-Za-z0-9_-]+$',
                message='SKU hanya boleh berisi huruf, angka, - dan _.'
            )
        ]
    )

    # KATEGORI PRODUK
    category = SelectField(
        'Kategori Utama',
        choices=[
            ('women', 'WOMEN'),
            ('men', 'MEN'),
            ('unisex', 'UNISEX')
        ],
        validators=[DataRequired()]
    )

    subcategory = SelectField(
        'Subkategori Busana',
        choices=[
            (category, category)
            for category in SUBCATEGORIES
        ],
        validators=[DataRequired()]
    )

    collection_label = StringField(
        'Label Koleksi / Edisi',
        validators=[Length(max=80)]
    )

    # HARGA PRODUK
    price = IntegerField(
        'Harga Jual (Rp)',
        validators=[
            InputRequired(),
            NumberRange(
                min=0,
                message='Harga tidak boleh negatif.'
            )
        ]
    )

    cost = IntegerField(
        'Harga Modal / HPP (Rp)',
        validators=[
            InputRequired(),
            NumberRange(
                min=0,
                message='Harga modal tidak boleh negatif.'
            )
        ]
    )

    # DESKRIPSI PRODUK
    description = TextAreaField(
        'Deskripsi Produk'
    )

    care_title = StringField(
        'Judul Instruksi Perawatan',
        validators=[Length(max=150)]
    )

    care_text = TextAreaField(
        'Instruksi Perawatan Produk'
    )

    # WARNA, UKURAN, DAN STOK
    variants_payload = HiddenField(
        'Data Varian Produk'
    )

    # FOTO PRODUK
    photos = MultipleFileField(
        'Foto Produk'
    )

    # TAMPILAN HOMEPAGE
    featured = BooleanField(
        'Tampilkan di Homepage'
    )


# VALIDASI DATA VARIAN PRODUK
def parse_variants(raw):

    try:
        values = json.loads(raw or '[]')
    except (ValueError, TypeError):
        return [], 'Format data varian tidak valid.'

    if not isinstance(values, list) or len(values) > 60:
        return [], 'Maksimal 60 kombinasi warna dan ukuran.'

    clean = []
    used = set()

    for item in values:

        if not isinstance(item, dict):
            return [], 'Data varian tidak valid.'

        name = str(
            item.get('color_name', '')
        ).strip()

        color_hex = str(
            item.get('color_hex', '')
        ).strip()

        size = str(
            item.get('size', '')
        ).upper()

        try:
            stock = int(item.get('stock', 0))
            minimum = int(
                item.get('minimum_stock', 5)
            )

        except (ValueError, TypeError):
            return [], 'Stok dan batas minimum harus angka bulat.'

        key = (name.casefold(), size)

        # Validasi nama dan kode warna
        if (
            not name
            or len(name) > 60
            or not re.fullmatch(
                r'#[0-9A-Fa-f]{6}',
                color_hex
            )
        ):
            return [], 'Nama atau kode HEX warna tidak valid.'

        # Validasi ukuran dan duplikasi varian
        if size not in SIZES or key in used:
            return [], 'Ukuran tidak valid atau varian terduplikasi.'

        # Validasi jumlah stok
        if not (
            0 <= stock <= 999999
            and 0 <= minimum <= 999999
        ):
            return [], 'Stok atau batas minimum di luar rentang.'

        used.add(key)

        clean.append({
            'color_name': name,
            'color_hex': color_hex,
            'size': size,
            'stock': stock,
            'minimum_stock': minimum
        })

    return clean, None
