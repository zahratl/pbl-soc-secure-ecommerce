from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SelectField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError, Regexp
from app.models.user import User

# Pola Regex: Wajib ada huruf besar, huruf kecil, angka, dan simbol khusus
PASSWORD_REGEX = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&_\-#])[A-Za-z\d@$!%*?&_\-#]{8,64}$'
PASSWORD_MESSAGE = (
    "Password harus 8-64 karakter dan mengandung minimal: "
    "1 huruf besar, 1 huruf kecil, 1 angka, dan 1 simbol (@$!%*?&#_-)."
)

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[
        DataRequired(message="Email wajib diisi"),
        Email(message="Format email tidak valid")
    ])
    password = PasswordField('Password', validators=[
        DataRequired(message="Password wajib diisi")
    ])
    remember_me = BooleanField('Ingat Saya')
    submit = SubmitField('Sign In')

class RegisterForm(FlaskForm):
    name = StringField('Username', validators=[
        DataRequired(message="Username wajib diisi"),
        Length(min=3, max=50, message="Minimal 3 karakter")
    ])
    email = StringField('Email', validators=[
        DataRequired(message="Email wajib diisi"),
        Email(message="Format email tidak valid")
    ])
    # VALIDASI PASSWORD KUAT SESUAI STANDAR RKS & OWASP
    password = PasswordField('Password', validators=[
        DataRequired(message="Password wajib diisi"),
        Length(min=8, max=64, message="Panjang password antara 8 hingga 64 karakter"),
        Regexp(PASSWORD_REGEX, message=PASSWORD_MESSAGE)
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(message="Konfirmasi password wajib diisi"),
        EqualTo('password', message="Password konfirmasi tidak cocok")
    ])
    gender = SelectField('Gender', choices=[
        ('', 'Your gender'),
        ('Male', 'Male'),
        ('Female', 'Female'),
        ('Other', 'Other')
    ], default='')
    submit = SubmitField('Register')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('Email ini sudah terdaftar. Silakan login atau gunakan email lain.')

class AdminLoginForm(FlaskForm):
    email = StringField('EMAIL / USERNAME ADMIN', validators=[
        DataRequired(message="Email atau Username wajib diisi")
    ])
    password = PasswordField('PASSWORD', validators=[
        DataRequired(message="Password wajib diisi")
    ])
    submit = SubmitField('SIGN IN')