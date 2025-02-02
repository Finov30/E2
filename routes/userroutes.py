from fastapi import APIRouter, File, UploadFile, HTTPException
from config.database import conn, engine, meta
from models.indexmodels import users, addresses
from schemas.userschemas import UserResponse, UserCreate, Address, AddressCreate
from sqlalchemy import select, MetaData
from cryptography.fernet import Fernet
from pydantic import BaseModel, constr
from typing import List
import csv
import io
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
from dotenv import load_dotenv
from faker import Faker
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, MetaData, Table
import random
from config.monitoring import (
    REQUESTS_PROCESSING_TIME,
    DB_OPERATIONS_PROCESSING_TIME,
    FAILED_OPERATIONS_COUNTER,
    USER_OPERATIONS_COUNTER
)
import time

load_dotenv()  # Chargement des variables d'environnement


user = APIRouter()
fake = Faker()

# Gestion sécurisée de la clé de chiffrement
key = os.getenv('ENCRYPTION_KEY')
if key is None:
    raise ValueError("ENCRYPTION_KEY non trouvée dans le fichier .env. Veuillez la définir.")

cipher_suite = Fernet(key.encode())

def encrypt_data(data: str) -> str:
    return cipher_suite.encrypt(data.encode()).decode()

def decrypt_data(data: str) -> str:
    return cipher_suite.decrypt(data.encode()).decode()

# Configuration de la deuxième base de données
SECOND_DATABASE_URL = os.getenv('SECOND_DATABASE_URL', 'mysql+pymysql://root:root@mysql_db:3306/faker_db')
second_engine = create_engine(SECOND_DATABASE_URL, pool_pre_ping=True)
second_meta = MetaData()

# Créer des tables spécifiques pour faker_db
faker_users = Table(
    'users', second_meta,
    *[c.copy() for c in users.columns]
)

faker_addresses = Table(
    'addresses', second_meta,
    *[c.copy() for c in addresses.columns]
)

# Modèles pour les utilisateurs
class UserCreate(BaseModel):
    name: constr(min_length=2, max_length=255)
    username: constr(min_length=3, max_length=255)
    password: constr(min_length=8, max_length=255)

class User(UserCreate):
    id: int

    class Config:
        from_attributes = True

class AddressCreate(BaseModel):
    street: constr(min_length=5, max_length=255)
    zipcode: constr(min_length=4, max_length=20)
    country: constr(min_length=2, max_length=100)
    # Suppression de user_id car il sera géré automatiquement

class Address(BaseModel):
    id: int
    user_id: int
    street: str
    zipcode: str
    country: str

    class Config:
        from_attributes = True

# Routes pour gérer les utilisateurs et leurs informations
@user.get("/")
async def read_data():
    try:
        query = select(users, addresses).select_from(
            users.join(addresses, users.c.id == addresses.c.user_id)
        )
        result = conn.execute(query).fetchall()
        
        formatted_data = []
        for row in result:
            data = {
                "id": row.id,
                "name": row.name,
                "username": decrypt_data(row.username),
                "address": {
                    "id": row.addresses_id,
                    "user_id": row.user_id,
                    "street": row.street,
                    "zipcode": row.zipcode,
                    "country": row.country
                }
            }
            formatted_data.append(data)
        
        return formatted_data
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la lecture des données: {str(e)}"
        )

@user.post("/users")
async def create_user(user_data: UserCreate, address_data: AddressCreate):
    try:
        with engine.begin() as transaction:
            # Vérifier si le username existe déjà
            existing_user = transaction.execute(
                select(users).where(users.c.username == encrypt_data(user_data.username))
            ).first()
            
            if existing_user:
                raise HTTPException(
                    status_code=400,
                    detail="Un utilisateur avec ce nom d'utilisateur existe déjà"
                )

            # Créer l'utilisateur
            user_result = transaction.execute(
                users.insert().values(
                    name=user_data.name,
                    username=encrypt_data(user_data.username),
                    password=encrypt_data(user_data.password)
                )
            )
            user_id = user_result.inserted_primary_key[0]
            
            # Créer l'adresse associée
            address_result = transaction.execute(
                addresses.insert().values(
                    user_id=user_id,
                    street=address_data.street,
                    zipcode=address_data.zipcode,
                    country=address_data.country
                )
            )
            
            return {
                "user": {
                    "id": user_id,
                    "name": user_data.name,
                    "username": user_data.username
                },
                "address": {
                    "id": address_result.inserted_primary_key[0],
                    "user_id": user_id,
                    "street": address_data.street,
                    "zipcode": address_data.zipcode,
                    "country": address_data.country
                }
            }

    except IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="Un utilisateur avec ce nom d'utilisateur existe déjà"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la création: {str(e)}"
        )


@user.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int):
    try:
        query = select(users).where(users.c.id == user_id)
        result = conn.execute(query).first()
        
        if not result:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
        
        return {
            "id": result.id,
            "name": result.name,
            "username": decrypt_data(result.username)
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la lecture de l'utilisateur: {str(e)}"
        )

@user.get("/addresses/{user_id}", response_model=Address)
async def get_user_address(user_id: int):
    try:
        query = select(addresses).where(addresses.c.user_id == user_id)
        result = conn.execute(query).first()
        
        if not result:
            raise HTTPException(
                status_code=404,
                detail="Adresse non trouvée pour cet utilisateur"
            )
        
        return {
            "id": result.id,
            "user_id": result.user_id,
            "street": result.street,
            "zipcode": result.zipcode,
            "country": result.country
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la lecture de l'adresse: {str(e)}"
        )

@user.post("/fetch-external-users")
async def fetch_external_users():
    start_time = time.time()
    try:
        users_data = []
        with engine.begin() as transaction:
            for _ in range(10):
                # Générer les données utilisateur avec Faker
                fake_name = fake.name()
                username = f"@{fake.unique.user_name()}"
                password = fake.password(length=12)
                
                # Créer l'utilisateur avec les données chiffrées
                user_data = {
                    "name": fake_name,
                    "username": encrypt_data(username),
                    "password": encrypt_data(password)
                }
                
                # Insérer l'utilisateur
                user_result = transaction.execute(users.insert().values(**user_data))
                user_id = user_result.inserted_primary_key[0]

                # Générer et créer l'adresse associée
                address_data = {
                    "user_id": user_id,
                    "street": fake.street_address(),
                    "zipcode": fake.postcode(),
                    "country": "France"
                }
                address_result = transaction.execute(addresses.insert().values(**address_data))
                
                # Ajouter aux données de retour
                users_data.append({
                    "user": {
                        "id": user_id,
                        "name": fake_name,
                        "username": username  # Version non chiffrée pour l'affichage
                    },
                    "address": {
                        "id": address_result.inserted_primary_key[0],
                        "user_id": user_id,
                        "street": address_data["street"],
                        "zipcode": address_data["zipcode"],
                        "country": address_data["country"]
                    }
                })

        # Métriques
        USER_OPERATIONS_COUNTER.labels(operation_type="fetch_external").inc(len(users_data))
        DB_OPERATIONS_PROCESSING_TIME.labels(operation_type="fetch_external").observe(
            time.time() - start_time
        )

        return {
            "message": f"{len(users_data)} utilisateurs et adresses créés avec succès",
            "users": users_data
        }
    except Exception as e:
        FAILED_OPERATIONS_COUNTER.labels(operation_type="fetch_external").inc()
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la création des utilisateurs: {str(e)}"
        )

@user.delete("/users/{user_id}")
async def delete_user(user_id: int):
    query = users.delete().where(users.c.id == user_id)
    result = conn.execute(query)
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted successfully"}

@user.delete("/addresses/{user_id}")
async def delete_user_id(user_id: int):
    query = addresses.delete().where(addresses.c.user_id == user_id)
    result = conn.execute(query)
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User ID not found")
    return {"message": "User ID deleted successfully"}

@user.post("/addresses/", response_model=Address)
async def create_address(address_data: AddressCreate, user_id: int):
    try:
        # Vérifier si l'utilisateur existe
        user_query = select(users).where(users.c.id == user_id)
        user_result = conn.execute(user_query).first()
        if not user_result:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        # Créer l'adresse
        query = addresses.insert().values(
            user_id=user_id,
            street=address_data.street,
            zipcode=address_data.zipcode,
            country=address_data.country
        )
        result = conn.execute(query)
        
        return {
            "id": result.inserted_primary_key[0],
            "user_id": user_id,
            "street": address_data.street,
            "zipcode": address_data.zipcode,
            "country": address_data.country
        }
    except IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="Une erreur est survenue lors de la création de l'adresse"
        )

@user.get("/addresses/", response_model=List[Address])
async def read_addresses():
    try:
        query = select(addresses)
        result = conn.execute(query).fetchall()
        
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "street": row.street,
                "zipcode": row.zipcode,
                "country": row.country
            }
            for row in result
        ]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la lecture des adresses: {str(e)}"
        )

@user.get("/addresses/{address_id}", response_model=Address)
async def read_address(address_id: int):
    query = select(addresses).where(addresses.c.id == address_id)
    result = conn.execute(query).first()
    if not result:
        raise HTTPException(status_code=404, detail="Address not found")
    return dict(result._asdict())

@user.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers CSV sont autorisés")

    try:
        contents = await file.read()
        csv_data = io.StringIO(contents.decode('utf-8'))
        csv_reader = csv.DictReader(csv_data)
        
        # Vérification des colonnes requises (sans les IDs)
        required_columns = {'name', 'username', 'password', 'street', 'zipcode', 'country'}
        csv_columns = set(csv_reader.fieldnames) if csv_reader.fieldnames else set()
        
        missing_columns = required_columns - csv_columns
        if missing_columns:
            raise HTTPException(
                status_code=400,
                detail=f"Colonnes manquantes dans le CSV: {', '.join(missing_columns)}"
            )

        users_data = []
        for row in csv_reader:
            try:
                # Validation des données utilisateur
                user_data = {
                    "name": row['name'],
                    "username": row['username'],
                    "password": row['password']
                }
                
                # Validation des données adresse
                address_data = {
                    "street": row['street'],
                    "zipcode": row['zipcode'],
                    "country": row['country']
                }

                # Insertion avec transaction
                with engine.begin() as transaction:
                    # Créer l'utilisateur
                    user_result = transaction.execute(
                        users.insert().values(
                            name=user_data['name'],
                            username=encrypt_data(user_data['username']),
                            password=encrypt_data(user_data['password'])
                        )
                    )
                    user_id = user_result.inserted_primary_key[0]
                    
                    # Créer l'adresse avec l'ID utilisateur généré
                    transaction.execute(
                        addresses.insert().values(
                            user_id=user_id,
                            street=address_data['street'],
                            zipcode=address_data['zipcode'],
                            country=address_data['country']
                        )
                    )
                
                users_data.append({
                    "user": {
                        "id": user_id,
                        "name": user_data['name'],
                        "username": user_data['username']
                    },
                    "address": {
                        "street": address_data['street'],
                        "zipcode": address_data['zipcode'],
                        "country": address_data['country']
                    }
                })

            except IntegrityError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Le nom d'utilisateur {row['username']} existe déjà dans la base de données"
                )
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Erreur à la ligne {csv_reader.line_num}: {str(e)}"
                )

        return {
            "message": f"{len(users_data)} utilisateurs importés avec succès",
            "users": users_data
        }

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Le fichier CSV doit être encodé en UTF-8"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors du traitement du fichier CSV: {str(e)}"
        )

@user.post("/scrape")
async def extraire_donnees_tableau():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)

    try:
        url = "https://webscraper.io/test-sites/tables"
        driver.get(url)

        wait = WebDriverWait(driver, 10)
        table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.table-bordered")))
        rows = table.find_elements(By.TAG_NAME, "tr")[1:]
        
        users_data = []
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            with engine.begin() as transaction:
                username = f"@{cells[3].text}"
                name = f"{cells[1].text} {cells[2].text}"
                
                # Créer l'utilisateur
                user_result = transaction.execute(
                    users.insert().values(
                        name=name,
                        username=encrypt_data(username),
                        password=encrypt_data(f"{name}123")
                    )
                )
                user_id = user_result.inserted_primary_key[0]

                # Créer l'adresse associée
                address_result = transaction.execute(
                    addresses.insert().values(
                        user_id=user_id,
                        street=f"{name} Street",
                        zipcode="12345",
                        country="France"
                    )
                )
                
                users_data.append({
                    "user": {
                        "id": user_id,
                        "name": name,
                        "username": username
                    },
                    "address": {
                        "id": address_result.inserted_primary_key[0],
                        "street": f"{name} Street",
                        "zipcode": "12345",
                        "country": "France"
                    }
                })

    finally:
        driver.quit()

    return {"message": "Données importées avec succès", "users": users_data}

@user.post("/users-with-address")
async def create_user_with_address(user_data: UserCreate, address_data: AddressCreate):
    try:
        with engine.begin() as transaction:
            # Vérifier si le username existe déjà
            existing_user = transaction.execute(
                select(users).where(users.c.username == encrypt_data(user_data.username))
            ).first()
            
            if existing_user:
                raise HTTPException(
                    status_code=400,
                    detail="Un utilisateur avec ce nom d'utilisateur existe déjà"
                )

            # Créer l'utilisateur
            user_result = transaction.execute(
                users.insert().values(
                    name=user_data.name,
                    username=encrypt_data(user_data.username),
                    password=encrypt_data(user_data.password)
                )
            )
            user_id = user_result.inserted_primary_key[0]
            
            # Créer l'adresse
            address_result = transaction.execute(
                addresses.insert().values(
                    user_id=user_id,
                    street=address_data.street,
                    zipcode=address_data.zipcode,
                    country=address_data.country
                )
            )
            
            return {
                "message": "Utilisateur et adresse créés avec succès",
                "user": {
                    "id": user_id,
                    "name": user_data.name,
                    "username": user_data.username
                },
                "address": {
                    "id": address_result.inserted_primary_key[0],
                    "user_id": user_id,
                    "street": address_data.street,
                    "zipcode": address_data.zipcode,
                    "country": address_data.country
                }
            }

    except IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="Un utilisateur avec ce nom d'utilisateur existe déjà"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la création: {str(e)}"
        )

@user.post("/extract-from-faker-db")
async def extract_from_faker_db(num_users: int = 10):
    start_time = time.time()
    try:
        # Créer les tables dans la DB faker
        second_meta.drop_all(second_engine, checkfirst=True)
        second_meta.create_all(second_engine, checkfirst=True)
        
        # Générer des données faker dans la seconde DB
        fake = Faker('fr_FR')
        faker_users_data = []
        
        with second_engine.begin() as second_conn:
            # Insérer des utilisateurs faker
            for _ in range(50):  # On crée un pool de 50 utilisateurs
                username = f"@{fake.unique.user_name()}"
                user_data = {
                    "name": fake.name(),
                    "username": encrypt_data(username),
                    "password": encrypt_data(fake.password(length=12))
                }
                user_result = second_conn.execute(faker_users.insert().values(**user_data))
                user_id = user_result.inserted_primary_key[0]
                
                # Créer une adresse pour chaque utilisateur
                address_data = {
                    "user_id": user_id,
                    "street": fake.street_address(),
                    "zipcode": fake.postcode(),
                    "country": "France"
                }
                address_result = second_conn.execute(faker_addresses.insert().values(**address_data))
                
                faker_users_data.append({
                    "user": user_data,
                    "address": address_data,
                    "user_id": user_id,
                    "address_id": address_result.inserted_primary_key[0]
                })

        # Sélectionner aléatoirement num_users utilisateurs
        selected_users = random.sample(faker_users_data, min(num_users, len(faker_users_data)))
        
        # Insérer les utilisateurs sélectionnés dans la DB principale
        imported_users = []
        with engine.begin() as main_conn:
            for user_data in selected_users:
                # Insérer l'utilisateur
                new_user = {
                    "name": user_data["user"]["name"],
                    "username": user_data["user"]["username"],
                    "password": user_data["user"]["password"]
                }
                user_result = main_conn.execute(users.insert().values(**new_user))
                new_user_id = user_result.inserted_primary_key[0]
                
                # Insérer l'adresse
                new_address = {
                    "user_id": new_user_id,
                    "street": user_data["address"]["street"],
                    "zipcode": user_data["address"]["zipcode"],
                    "country": user_data["address"]["country"]
                }
                address_result = main_conn.execute(addresses.insert().values(**new_address))
                
                imported_users.append({
                    "user": {
                        "id": new_user_id,
                        "name": user_data["user"]["name"],
                        "username": decrypt_data(user_data["user"]["username"])
                    },
                    "address": {
                        "id": address_result.inserted_primary_key[0],
                        "street": new_address["street"],
                        "zipcode": new_address["zipcode"],
                        "country": new_address["country"]
                    }
                })
        
        USER_OPERATIONS_COUNTER.labels(operation_type="extract_faker").inc(num_users)
        DB_OPERATIONS_PROCESSING_TIME.labels(operation_type="extract_faker").observe(
            time.time() - start_time
        )
        return {
            "message": f"{len(imported_users)} utilisateurs importés avec succès",
            "users": imported_users
        }
        
    except Exception as e:
        FAILED_OPERATIONS_COUNTER.labels(operation_type="extract_faker").inc()
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de l'extraction des données: {str(e)}"
        )
