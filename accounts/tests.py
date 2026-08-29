from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class AuthenticationSmokeTests(APITestCase):
    def test_health_check_is_public(self):
        response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'status': 'ok'})

    def test_user_can_obtain_jwt_pair(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username='demo',
            password='strong-test-password',
        )

        response = self.client.post(
            reverse('login'),
            {'username': 'demo', 'password': 'strong-test-password'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_user_can_register(self):
        response = self.client.post(
            reverse('register'),
            {
                'username': 'new-user',
                'email': 'new@example.com',
                'firstName': 'New',
                'lastName': 'User',
                'password': 'strong-test-password',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(username='new-user')
        self.assertTrue(user.check_password('strong-test-password'))
        self.assertEqual(user.first_name, 'New')
        self.assertEqual(user.last_name, 'User')
        self.assertEqual(response.data['firstName'], 'New')
        self.assertEqual(response.data['lastName'], 'User')
        self.assertNotIn('password', response.data)

    def test_registration_rejects_short_password(self):
        response = self.client.post(
            reverse('register'),
            {
                'username': 'new-user',
                'email': 'new@example.com',
                'firstName': 'New',
                'lastName': 'User',
                'password': 'short',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_authenticated_user_can_read_me(self):
        user = get_user_model().objects.create_user(
            username='me-user',
            email='me@example.com',
            password='strong-test-password',
        )
        login = self.client.post(
            reverse('login'),
            {'username': 'me-user', 'password': 'strong-test-password'},
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        response = self.client.get(reverse('me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], user.username)

    def test_me_requires_authentication(self):
        response = self.client.get(reverse('me'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
